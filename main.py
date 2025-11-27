
import os
import asyncio
import re
import logging
import sys
from datetime import datetime, timedelta, timezone
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from aiohttp import web
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

if not API_ID or API_ID == 0:
    logger.error("API_ID manquant")
    exit(1)
if not API_HASH:
    logger.error("API_HASH manquant")
    exit(1)
if not BOT_TOKEN:
    logger.error("BOT_TOKEN manquant")
    exit(1)

logger.info(f"Configuration: SOURCE_CHANNEL={SOURCE_CHANNEL_ID}, PREDICTION_CHANNEL={PREDICTION_CHANNEL_ID}")

# Charger la session depuis l'environnement ou créer une nouvelle
session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

pending_predictions = {}
recent_games = {}
processed_messages = set()  # Éviter les doublons
last_transferred_game = None  # Dernier jeu transféré

def extract_game_number(message: str):
    match = re.search(r"#N\s*(\d+)\.?", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extract_parentheses_groups(message: str):
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(group_str: str) -> str:
    normalized = group_str.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    return normalized

def get_suits_in_group(group_str: str):
    normalized = normalize_suits(group_str)
    return [s for s in ALL_SUITS if s in normalized]

def count_cards(group_str: str) -> int:
    normalized = normalize_suits(group_str)
    return sum(normalized.count(s) for s in ALL_SUITS)

def find_missing_suit(group_str: str):
    suits_present = get_suits_in_group(group_str)
    if len(suits_present) == 3:
        missing = [s for s in ALL_SUITS if s not in suits_present][0]
        return SUIT_DISPLAY.get(missing, missing)
    return None

def has_suit_in_group(group_str: str, target_suit: str) -> bool:
    normalized = normalize_suits(group_str)
    target_normalized = normalize_suits(target_suit)
    for suit in ALL_SUITS:
        if suit in target_normalized and suit in normalized:
            return True
    return False

def get_alternate_suit(suit: str) -> str:
    return SUIT_MAPPING.get(suit, suit)

async def send_prediction(game_number: int, missing_suit: str, base_game1: int, base_game2: int):
    try:
        target_game = base_game1 + 5
        alternate_suit = get_alternate_suit(missing_suit)
        backup_game = target_game + 5
        
        prediction_msg = f"""😼 {target_game}😺: √{missing_suit} statut :🔮

📊 Basé sur: Jeux #{base_game1} et #{base_game2}
🎯 Couleur manquante: {missing_suit}
🔄 Si {target_game} et {target_game+1} échouent: {backup_game}{alternate_suit}"""
        
        msg_id = 0
        
        # Envoyer les prédictions au CANAL de prédiction
        if PREDICTION_CHANNEL_ID and PREDICTION_CHANNEL_ID != 0:
            try:
                pred_msg = await client.send_message(PREDICTION_CHANNEL_ID, prediction_msg)
                msg_id = pred_msg.id
                logger.info(f"✅ Prédiction envoyée au canal de prédiction {PREDICTION_CHANNEL_ID}")
            except Exception as e:
                logger.error(f"❌ Erreur envoi prédiction au canal: {e}")
        
        pending_predictions[target_game] = {
            'message_id': msg_id,
            'suit': missing_suit,
            'alternate_suit': alternate_suit,
            'backup_game': backup_game,
            'base_game1': base_game1,
            'base_game2': base_game2,
            'status': '🔮',
            'check_count': 0,
            'created_at': datetime.now().isoformat()
        }
        
        logger.info(f"Prédiction envoyée: Jeu #{target_game} - {missing_suit} (basé sur #{base_game1}+#{base_game2})")
        return msg_id
        
    except Exception as e:
        logger.error(f"Erreur envoi prédiction: {e}")
        return None

async def update_prediction_status(game_number: int, new_status: str):
    try:
        if game_number not in pending_predictions:
            return False
        
        pred = pending_predictions[game_number]
        message_id = pred['message_id']
        suit = pred['suit']
        
        updated_msg = f"""😼 {game_number}😺: √{suit} statut :{new_status}

📊 Basé sur: Jeux #{pred['base_game1']} et #{pred['base_game2']}
🎯 Couleur prédite: {suit}
🔄 Alternative: {pred['backup_game']}{pred['alternate_suit']}"""
        
        # Éditer le message dans le CANAL de prédiction
        if PREDICTION_CHANNEL_ID and PREDICTION_CHANNEL_ID != 0 and message_id > 0:
            try:
                await client.edit_message(PREDICTION_CHANNEL_ID, message_id, updated_msg)
                logger.info(f"✅ Prédiction #{game_number} mise à jour dans le canal: {new_status}")
            except Exception as e:
                logger.error(f"❌ Erreur mise à jour dans le canal: {e}")
        
        pred['status'] = new_status
        logger.info(f"Prédiction #{game_number} mise à jour: {new_status}")
        
        if new_status in ['✅0️⃣', '✅1️⃣', '❌']:
            del pending_predictions[game_number]
            logger.info(f"Prédiction #{game_number} terminée et supprimée")
        
        return True
        
    except Exception as e:
        logger.error(f"Erreur mise à jour prédiction: {e}")
        return False

def is_message_finalized(message: str) -> bool:
    # Si le message contient ⏰, il sera encore modifié - ATTENDRE
    if '⏰' in message:
        return False
    # Le message est finalisé s'il contient ✅ OU 🔰
    return '✅' in message or '🔰' in message

def analyze_for_prediction(game_number: int, first_group: str):
    first_count = count_cards(first_group)
    
    # Analyser tous les jeux ayant exactement 3 cartes
    if first_count == 3:
        suits_present = get_suits_in_group(first_group)
        # Identifier la couleur manquante (peu importe le nombre de couleurs présentes)
        missing_suits = [s for s in ALL_SUITS if s not in suits_present]
        if missing_suits:
            missing_suit = SUIT_DISPLAY.get(missing_suits[0], missing_suits[0])
            return {
                'game_number': game_number,
                'missing_suit': missing_suit,
                'first_group': first_group
            }
    return None

async def check_prediction_result(game_number: int, first_group: str):
    if game_number in pending_predictions:
        pred = pending_predictions[game_number]
        target_suit = pred['suit']
        
        if has_suit_in_group(first_group, target_suit):
            await update_prediction_status(game_number, '✅0️⃣')
            logger.info(f"Prédiction #{game_number} réussie immédiatement!")
            return True
        else:
            pred['check_count'] = 1
            logger.info(f"Prédiction #{game_number}: couleur non trouvée, attente du jeu suivant")
    
    prev_game = game_number - 1
    if prev_game in pending_predictions:
        pred = pending_predictions[prev_game]
        if pred.get('check_count', 0) >= 1:
            target_suit = pred['suit']
            
            if has_suit_in_group(first_group, target_suit):
                await update_prediction_status(prev_game, '✅1️⃣')
                logger.info(f"Prédiction #{prev_game} réussie au jeu +1!")
                return True
            else:
                await update_prediction_status(prev_game, '❌')
                logger.info(f"Prédiction #{prev_game} échouée - Envoi backup")
                
                # Envoyer prédiction backup automatiquement
                backup_target = pred['backup_game']
                alternate_suit = pred['alternate_suit']
                await send_prediction(
                    backup_target,
                    alternate_suit,
                    pred['base_game1'],
                    pred['base_game2']
                )
                logger.info(f"Backup envoyé: #{backup_target} en {alternate_suit}")
                return False
    
    return None

async def process_finalized_message(message_text: str, chat_id: int):
    global last_transferred_game
    try:
        if not is_message_finalized(message_text):
            return
        
        game_number = extract_game_number(message_text)
        if game_number is None:
            return
        
        # Éviter les doublons
        message_hash = f"{game_number}_{message_text[:50]}"
        if message_hash in processed_messages:
            return
        processed_messages.add(message_hash)
        
        # Limiter la taille du set
        if len(processed_messages) > 200:
            processed_messages.clear()
        
        groups = extract_parentheses_groups(message_text)
        if len(groups) < 2:
            return
        
        first_group = groups[0]
        second_group = groups[1]
        
        logger.info(f"Jeu #{game_number} finalisé (chat_id: {chat_id}) - Groupe1: {first_group}")
        
        # Transférer au bot SI transfert activé
        if transfer_enabled and ADMIN_ID and ADMIN_ID != 0 and last_transferred_game != game_number:
            try:
                transfer_msg = f"📨 **Message finalisé du canal source:**\n\n{message_text}"
                await client.send_message(ADMIN_ID, transfer_msg)
                last_transferred_game = game_number
                logger.info(f"✅ Message finalisé #{game_number} transféré à votre bot {ADMIN_ID}")
            except Exception as e:
                logger.error(f"❌ Erreur transfert à votre bot: {e}")
        elif not transfer_enabled:
            logger.info(f"🔇 Message #{game_number} traité en silence (transfert désactivé)")
        
        await check_prediction_result(game_number, first_group)
        
        recent_games[game_number] = {
            'first_group': first_group,
            'second_group': second_group,
            'timestamp': datetime.now().isoformat()
        }
        
        if len(recent_games) > 100:
            oldest = min(recent_games.keys())
            del recent_games[oldest]
        
        analysis = analyze_for_prediction(game_number, first_group)
        
        if analysis:
            prev_game_num = game_number - 2
            if prev_game_num in recent_games:
                prev_game = recent_games[prev_game_num]
                prev_analysis = analyze_for_prediction(prev_game_num, prev_game['first_group'])
                
                if prev_analysis:
                    target_game = game_number + 5
                    if target_game not in pending_predictions:
                        await send_prediction(
                            target_game,
                            analysis['missing_suit'],
                            prev_game_num,
                            game_number
                        )
        
    except Exception as e:
        logger.error(f"Erreur traitement message: {e}")
        import traceback
        logger.error(traceback.format_exc())

@client.on(events.NewMessage())
async def handle_message(event):
    try:
        # Obtenir l'ID du chat de manière fiable
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id
        
        # Convertir en ID négatif pour les canaux si nécessaire
        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id
        
        logger.info(f"Message reçu de chat_id={chat_id}, attendu={SOURCE_CHANNEL_ID}")
        
        # Vérifier si c'est le canal source
        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message
            logger.info(f"Message du canal source: {message_text[:80]}...")
            await process_finalized_message(message_text, chat_id)
        
    except Exception as e:
        logger.error(f"Erreur handle_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

@client.on(events.MessageEdited())
async def handle_edited_message(event):
    try:
        # Obtenir l'ID du chat de manière fiable
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id
        
        # Convertir en ID négatif pour les canaux si nécessaire
        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id
        
        logger.info(f"Message édité de chat_id={chat_id}, attendu={SOURCE_CHANNEL_ID}")
        
        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message
            logger.info(f"Message édité dans canal source: {message_text[:80]}...")
            await process_finalized_message(message_text, chat_id)
            
    except Exception as e:
        logger.error(f"Erreur handle_edited_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    if event.is_group or event.is_channel:
        return
    
    logger.info(f"Commande /start reçue de {event.sender_id}")
    await event.respond("""🤖 **Bot de Prédiction Baccarat**

Ce bot surveille un canal source et envoie des prédictions automatiques.

**Commandes:**
• `/status` - Voir les prédictions en cours
• `/help` - Aide détaillée
• `/debug` - Informations de débogage""")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.is_group or event.is_channel:
        return
    
    logger.info(f"Commande /status reçue de {event.sender_id}")
    
    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("Commande réservée à l'administrateur")
        return
    
    if not pending_predictions:
        await event.respond("📊 **Aucune prédiction en cours**")
        return
    
    status_msg = "📊 **Prédictions en cours:**\n\n"
    for game_num, pred in pending_predictions.items():
        status_msg += f"• Jeu #{game_num}: {pred['suit']} - Statut: {pred['status']}\n"
    
    await event.respond(status_msg)

@client.on(events.NewMessage(pattern='/debug'))
async def cmd_debug(event):
    if event.is_group or event.is_channel:
        return
    
    logger.info(f"Commande /debug reçue de {event.sender_id}")
    
    debug_msg = f"""🔍 **Informations de débogage:**

**Configuration:**
• Source Channel: {SOURCE_CHANNEL_ID}
• Prediction Channel: {PREDICTION_CHANNEL_ID}
• Admin ID: {ADMIN_ID}

**État:**
• Prédictions actives: {len(pending_predictions)}
• Jeux récents: {len(recent_games)}
• Port: {PORT}
"""
    
    await event.respond(debug_msg)

transfer_enabled = True  # Transfert activé par défaut

@client.on(events.NewMessage(pattern='/transfert'))
async def cmd_transfert(event):
    if event.is_group or event.is_channel:
        return
    
    global transfer_enabled
    transfer_enabled = True
    logger.info(f"Transfert activé par {event.sender_id}")
    await event.respond("✅ Transfert des messages finalisés activé!\n\nVous recevrez tous les messages finalisés du canal source.")

@client.on(events.NewMessage(pattern='/activetransfert'))
async def cmd_active_transfert(event):
    if event.is_group or event.is_channel:
        return
    
    global transfer_enabled
    if transfer_enabled:
        await event.respond("✅ Le transfert est déjà activé!")
    else:
        transfer_enabled = True
        logger.info(f"Transfert réactivé par {event.sender_id}")
        await event.respond("✅ Transfert réactivé avec succès!")

@client.on(events.NewMessage(pattern='/stoptransfert'))
async def cmd_stop_transfert(event):
    if event.is_group or event.is_channel:
        return
    
    global transfer_enabled
    transfer_enabled = False
    logger.info(f"Transfert désactivé par {event.sender_id}")
    await event.respond("⛔ Transfert des messages désactivé.")

@client.on(events.NewMessage(pattern='/help'))
async def cmd_help(event):
    if event.is_group or event.is_channel:
        return
    
    logger.info(f"Commande /help reçue de {event.sender_id}")
    
    await event.respond("""📖 **Aide - Bot de Prédiction**

**Fonctionnement:**
1. Le bot surveille le canal source
2. Analyse tous les jeux ayant 3 cartes dans le premier groupe
3. Identifie la couleur manquante et envoie une prédiction

**Commandes:**
• `/start` - Démarrer le bot
• `/status` - Voir les prédictions en cours
• `/transfert` - Activer transfert des messages
• `/activetransfert` - Réactiver le transfert
• `/stoptransfert` - Désactiver le transfert
• `/debug` - Informations de débogage

**Règles de prédiction:**
• Analyse 2 jeux consécutifs avec 3 cartes
• Identifie la couleur manquante (♠️, ❤️, ♦️ ou ♣️)
• Prédit: premier_jeu + 5 avec la couleur manquante
• Si échec au numéro ET numéro+1 → Backup automatique: +5 avec couleur opposée

**Exemple:**
Jeu #767: K♥️K♣️5♣️ → manque ♠️
Jeu #768: J♣️A♦️3♥️ → manque ♠️
→ Prédiction: #772 (767+5) en ♠️
→ Si #772 et #773 échouent: #777 (772+5) en ❤️ (automatique)

**Vérification automatique:**
• ✅0️⃣ = Couleur trouvée au numéro prédit → STOP
• ✅1️⃣ = Couleur trouvée au numéro +1 → STOP
• ❌ = Échec → Backup automatique envoyé""")

async def index(request):
    html = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Prédiction Baccarat</title>
        <meta charset="utf-8">
    </head>
    <body>
        <h1>🎯 Bot de Prédiction Baccarat</h1>
        <p>Le bot est en ligne et surveille les canaux.</p>
        <ul>
            <li><a href="/health">Health Check</a></li>
            <li><a href="/status">Statut (JSON)</a></li>
        </ul>
    </body>
    </html>
    """
    return web.Response(text=html, content_type='text/html', status=200)

async def health_check(request):
    return web.Response(text="OK", status=200)

async def status_api(request):
    status_data = {
        "status": "running",
        "source_channel": SOURCE_CHANNEL_ID,
        "prediction_channel": PREDICTION_CHANNEL_ID,
        "pending_predictions": len(pending_predictions),
        "recent_games": len(recent_games),
        "timestamp": datetime.now().isoformat()
    }
    return web.json_response(status_data)

async def start_web_server():
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)
    app.router.add_get('/status', status_api)
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()
    logger.info(f"Serveur web démarré sur 0.0.0.0:{PORT}")

async def start_bot():
    try:
        logger.info("Démarrage du bot...")
        await client.start(bot_token=BOT_TOKEN)
        logger.info("Bot Telegram connecté")
        
        # Sauvegarder la session
        session = client.session.save()
        logger.info(f"Session Telegram: {session[:50]}... (sauvegardez ceci dans TELEGRAM_SESSION)")
        
        me = await client.get_me()
        username = getattr(me, 'username', 'Unknown') or f"ID:{getattr(me, 'id', 'Unknown')}"
        logger.info(f"Bot opérationnel: @{username}")
        
        # Vérifier l'accès aux canaux
        try:
            source_entity = await client.get_entity(SOURCE_CHANNEL_ID)
            logger.info(f"✅ Accès au canal source confirmé: {getattr(source_entity, 'title', 'N/A')}")
        except Exception as e:
            logger.error(f"❌ Impossible d'accéder au canal source: {e}")
        
        try:
            # Forcer la récupération du canal de prédiction
            pred_entity = await client.get_entity(PREDICTION_CHANNEL_ID)
            logger.info(f"✅ Accès au canal de prédiction confirmé: {getattr(pred_entity, 'title', 'N/A')}")
            
            # Envoyer un message de test pour confirmer les permissions
            test_msg = await client.send_message(PREDICTION_CHANNEL_ID, "🤖 Bot connecté et prêt à envoyer des prédictions!")
            await asyncio.sleep(2)
            await client.delete_messages(PREDICTION_CHANNEL_ID, test_msg.id)
            logger.info("✅ Permissions d'écriture confirmées dans le canal de prédiction")
        except Exception as e:
            logger.error(f"❌ Impossible d'accéder au canal de prédiction: {e}")
            logger.error("Vérifiez que le bot est ADMINISTRATEUR dans le canal de prédiction!")
        
        logger.info(f"Surveillance du canal source: {SOURCE_CHANNEL_ID}")
        logger.info(f"Envoi des prédictions vers: {PREDICTION_CHANNEL_ID}")
        
        return True
    except Exception as e:
        logger.error(f"Erreur démarrage: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return False

async def main():
    try:
        await start_web_server()
        
        success = await start_bot()
        if not success:
            logger.error("Échec du démarrage du bot")
            return
        
        logger.info("Bot complètement opérationnel - En attente de messages...")
        await client.run_until_disconnected()
        
    except Exception as e:
        logger.error(f"Erreur dans main: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        await client.disconnect()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot arrêté par l'utilisateur")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
        import traceback
        logger.error(traceback.format_exc())
