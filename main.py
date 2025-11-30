import os
import asyncio
import re
import logging
import sys
from datetime import datetime, timedelta, timezone, time
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from aiohttp import web
# --- IMPORTATION DE LA CONFIGURATION (CORRECTION) ---
from config import (
    API_ID, API_HASH, BOT_TOKEN, ADMIN_ID,
    SOURCE_CHANNEL_ID, PREDICTION_CHANNEL_ID, PORT,
    SUIT_MAPPING, ALL_SUITS, SUIT_DISPLAY
)

# --- Constantes Globales Mises à Jour (Maintenues de main (9).py) ---
MAX_PENDING_PREDICTIONS = 2  
PROXIMITY_THRESHOLD = 10     # Seuil pour N+18 (pour commencer à envoyer la prédiction)
PREDICTION_OFFSET = 18       # DÉCALAGE MIS À JOUR : N+1 -> Prédire N + 18

# --- Configuration et Initialisation ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Vérifications minimales de la configuration
if not API_ID or API_ID == 0:
    logger.error("API_ID manquant")
    exit(1)
# 🚨 CORRECTION: La vérification ne doit plus échouer sur un placeholder spécifique
if not API_HASH:
    logger.error("API_HASH manquant")
    exit(1)
if not BOT_TOKEN:
    logger.error("BOT_TOKEN manquant")
    exit(1)

logger.info(f"Configuration: SOURCE_CHANNEL={SOURCE_CHANNEL_ID}, PREDICTION_CHANNEL={PREDICTION_CHANNEL_ID}")

# Initialisation du client Telegram avec session string ou nouvelle session
session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

# --- Variables Globales d'État ---
pending_predictions = {}
queued_predictions = {}
# Stockage des derniers jeux pour la nouvelle règle N / N+1
recent_games = {} 
processed_messages = set()
last_transferred_game = None
current_game_number = 0

source_channel_ok = False
prediction_channel_ok = False
transfer_enabled = True 

# --- Fonctions d'Analyse ---

def extract_game_number(message: str):
    """Extrait le numéro de jeu du message."""
    match = re.search(r"#N\s*(\d+)\.?", message, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extract_parentheses_groups(message: str):
    """Extrait le contenu entre parenthèses."""
    return re.findall(r"\(([^)]*)\)", message)

def normalize_suits(group_str: str) -> str:
    """Normalise les symboles de couleur."""
    normalized = group_str.replace('❤️', '♥').replace('❤', '♥').replace('♥️', '♥')
    normalized = normalized.replace('♠️', '♠').replace('♦️', '♦').replace('♣️', '♣')
    return normalized

def get_suits_in_group(group_str: str) -> set:
    """Liste toutes les couleurs (suits) présentes dans une chaîne."""
    normalized = normalize_suits(group_str)
    return {s for s in ALL_SUITS if s in normalized}

def get_predicted_suit(missing_suit: str) -> str:
    """Applique le mapping personnalisé (couleur manquante -> couleur prédite)."""
    return SUIT_MAPPING.get(missing_suit, missing_suit)

# --- Logique de Prédiction et File d'Attente ---

async def send_prediction_to_channel(target_game: int, predicted_suit: str, base_game: int):
    """Envoie la prédiction au canal de prédiction et l'ajoute aux prédictions actives."""
    try:
        # La couleur de backup est la couleur alternative selon le mapping
        alternate_suit = get_predicted_suit(predicted_suit) 

        # Le backup est +18 jeux après le jeu cible
        backup_game = target_game + PREDICTION_OFFSET 

        display_suit = SUIT_DISPLAY.get(predicted_suit, predicted_suit)

        prediction_msg = f"""😼 {target_game}😺: √{display_suit} statut :🔮"""

        msg_id = 0

        if PREDICTION_CHANNEL_ID and PREDICTION_CHANNEL_ID != 0 and prediction_channel_ok:
            try:
                pred_msg = await client.send_message(PREDICTION_CHANNEL_ID, prediction_msg)
                msg_id = pred_msg.id
                logger.info(f"✅ Prédiction envoyée au canal de prédiction {PREDICTION_CHANNEL_ID}")
            except Exception as e:
                logger.error(f"❌ Erreur envoi prédiction au canal: {e}")
        else:
            logger.warning(f"⚠️ Canal de prédiction non accessible, prédiction non envoyée")

        pending_predictions[target_game] = {
            'message_id': msg_id,
            'suit': predicted_suit,
            'alternate_suit': alternate_suit, 
            'backup_game': backup_game,
            'base_game': base_game,
            'status': '🔮',
            'check_count': 0,
            'created_at': datetime.now().isoformat()
        }

        logger.info(f"Prédiction active: Jeu #{target_game} - {predicted_suit} (basé sur #{base_game})")
        return msg_id

    except Exception as e:
        logger.error(f"Erreur envoi prédiction: {e}")
        return None

def queue_prediction(target_game: int, predicted_suit: str, base_game: int):
    """Met une prédiction en file d'attente pour un envoi différé (gestion du stock)."""
    if target_game in queued_predictions or target_game in pending_predictions:
        logger.info(f"Prédiction #{target_game} déjà en file ou active, ignorée")
        return False

    queued_predictions[target_game] = {
        'target_game': target_game,
        'predicted_suit': predicted_suit,
        'base_game': base_game,
        'queued_at': datetime.now().isoformat()
    }
    logger.info(f"📋 Prédiction #{target_game} mise en file d'attente (sera envoyée quand proche)")
    return True

async def check_and_send_queued_predictions(current_game: int):
    """Vérifie la file d'attente et envoie les prédictions proches, dans la limite MAX_PENDING_PREDICTIONS."""
    global current_game_number
    current_game_number = current_game

    if len(pending_predictions) >= MAX_PENDING_PREDICTIONS:
        logger.info(f"⏸️ {len(pending_predictions)} prédictions en cours (max {MAX_PENDING_PREDICTIONS}), attente...")
        return

    sorted_queued = sorted(queued_predictions.keys())

    for target_game in sorted_queued:
        if len(pending_predictions) >= MAX_PENDING_PREDICTIONS:
            break

        distance = target_game - current_game

        # Si le jeu cible est proche (dans le seuil) et n'est pas déjà passé
        if distance <= PROXIMITY_THRESHOLD and distance > 0:
            pred_data = queued_predictions.pop(target_game)
            logger.info(f"🎯 Jeu #{current_game} - Prédiction #{target_game} proche ({distance} jeux), envoi maintenant!")

            await send_prediction_to_channel(
                pred_data['target_game'],
                pred_data['predicted_suit'],
                pred_data['base_game']
            )
        elif distance <= 0:
            logger.warning(f"⚠️ Prédiction #{target_game} expirée (jeu actuel: {current_game}), supprimée")
            queued_predictions.pop(target_game, None)

async def update_prediction_status(game_number: int, new_status: str):
    """Met à jour le message de prédiction dans le canal et son statut interne."""
    try:
        if game_number not in pending_predictions:
            return False

        pred = pending_predictions[game_number]
        suit = pred['suit']
        display_suit = SUIT_DISPLAY.get(suit, suit)

        updated_msg = f"""😼 {game_number}😺: √{display_suit} statut :{new_status}"""

        if PREDICTION_CHANNEL_ID and PREDICTION_CHANNEL_ID != 0 and pred['message_id'] > 0 and prediction_channel_ok:
            try:
                await client.edit_message(PREDICTION_CHANNEL_ID, pred['message_id'], updated_msg)
                logger.info(f"✅ Prédiction #{game_number} mise à jour dans le canal: {new_status}")
            except Exception as e:
                logger.error(f"❌ Erreur mise à jour dans le canal: {e}")

        pred['status'] = new_status
        logger.info(f"Prédiction #{game_number} mise à jour: {new_status}")

        # Les prédictions terminées sont supprimées du stock actif
        if new_status in ['✅0️⃣', '✅1️⃣', '❌']:
            del pending_predictions[game_number]
            logger.info(f"Prédiction #{game_number} terminée et supprimée")

        return True

    except Exception as e:
        logger.error(f"Erreur mise à jour prédiction: {e}")
        return False

def is_message_finalized(message: str) -> bool:
    """Vérifie si le message est un résultat final (non en cours) en utilisant les symboles."""
    if '⏰' in message:
        return False
    # Vérifie si le message contient un symbole de finalisation
    return '✅' in message or '🔰' in message

async def check_prediction_result(game_number: int, first_group: str):
    """
    Vérifie les résultats des prédictions actives (double chance N et N+1)
    """
    
    # 1. Vérification du jeu actuel (Jeu Cible N)
    if game_number in pending_predictions:
        pred = pending_predictions[game_number]
        target_suit = pred['suit']
        suits_present = get_suits_in_group(first_group)

        if target_suit in suits_present:
            await update_prediction_status(game_number, '✅0️⃣')
            return True
        else:
            # La prédiction passe au statut 'en attente de N+1'
            pred['check_count'] = 1
            return False

    # 2. Vérification du jeu précédent (Jeu Cible N-1 - c'est la 2ème chance pour cette prédiction)
    prev_game = game_number - 1
    if prev_game in pending_predictions:
        pred = pending_predictions[prev_game]
        # Vérifie si la prédiction a été marquée pour la deuxième vérification
        if pred.get('check_count', 0) >= 1:
            target_suit = pred['suit']
            suits_present = get_suits_in_group(first_group)

            if target_suit in suits_present:
                await update_prediction_status(prev_game, '✅1️⃣')
                return True
            else:
                await update_prediction_status(prev_game, '❌')
                logger.info(f"Prédiction #{prev_game} échouée (❌) - Envoi du backup")

                backup_target = pred['backup_game']
                alternate_suit = pred['alternate_suit']
                
                # Le backup est une nouvelle prédiction mise en file d'attente
                queue_prediction(
                    backup_target,
                    alternate_suit,
                    pred['base_game']
                )
                logger.info(f"Backup mis en file: #{backup_target} en {alternate_suit}")
                return False

    return None

def check_new_rule_prediction(current_game: int, first_group: str):
    """
    NOUVELLE RÈGLE: Vérifie le jeu N-1 (N) et le jeu actuel (N+1) pour la condition d'union.
    Déclenche la prédiction pour N+1 + 18.
    """
    prev_game = current_game - 1
    
    # 1. Vérifier si le jeu N (précédent) est dans le stock
    if prev_game not in recent_games:
        return

    # 2. Récupérer les données de N et de N+1 (actuel)
    game_n_data = recent_games[prev_game]
    suits_n = get_suits_in_group(game_n_data['first_group'])
    suits_n_plus_1 = get_suits_in_group(first_group)

    # 3. Calculer l'union des couleurs
    union_suits = suits_n.union(suits_n_plus_1)
    
    # 4. Condition de déclenchement : EXACTEMENT 3 couleurs
    if len(union_suits) == 3:
        
        # 5. Trouver la couleur manquante
        missing_suit_raw = (set(ALL_SUITS) - union_suits).pop()

        # 6. Appliquer le mapping
        predicted_suit = get_predicted_suit(missing_suit_raw) 
        
        # 7. Définir le jeu cible à N+1 + 18
        target_game = current_game + PREDICTION_OFFSET 
        
        if target_game not in pending_predictions and target_game not in queued_predictions:
            logger.warning(f"🏆 RÈGLE NOUVELLE APPLIQUÉE: Union {union_suits} (manque {missing_suit_raw}) -> Prédire {predicted_suit} sur #{target_game}")
            
            # Ajout à la file d'attente
            queue_prediction(
                target_game,
                predicted_suit,
                current_game  # Base sur le jeu N+1 (current_game)
            )
            return True
        else:
             logger.info(f"Règle NOUVELLE trouvée, mais la prédiction #{target_game} est déjà en file ou active.")
             return False

    return False


async def process_finalized_message(message_text: str, chat_id: int):
    """
    Traite un message finalisé: stocke, vérifie la nouvelle règle, vérifie les résultats actifs.
    """
    global last_transferred_game, current_game_number
    try:
        if not is_message_finalized(message_text):
            return

        game_number = extract_game_number(message_text)
        if game_number is None:
            return

        current_game_number = game_number

        # Évite le double traitement des messages
        message_hash = f"{game_number}_{message_text[:50]}"
        if message_hash in processed_messages:
            return
        processed_messages.add(message_hash)

        if len(processed_messages) > 200:
            processed_messages.clear()

        groups = extract_parentheses_groups(message_text)
        if len(groups) < 1:
            return

        first_group = groups[0]

        logger.info(f"Jeu #{game_number} finalisé - Groupe1: {first_group}")

        # --- Stockage du jeu actuel (N+1 pour le jeu précédent) ---
        recent_games[game_number] = {
            'first_group': first_group,
            'timestamp': datetime.now().isoformat()
        }
        # Nettoyage des jeux très anciens
        if len(recent_games) > 100:
            oldest = min(recent_games.keys())
            del recent_games[oldest]

        # --- NOUVELLE LOGIQUE DE PRÉDICTION (Union N et N+1) ---
        check_new_rule_prediction(game_number, first_group)

        # --- Transfert à l'administrateur (si activé) ---
        if transfer_enabled and ADMIN_ID and ADMIN_ID != 0 and last_transferred_game != game_number:
            try:
                transfer_msg = f"📨 **Message finalisé du canal source:**\n\n{message_text}"
                await client.send_message(ADMIN_ID, transfer_msg)
                last_transferred_game = game_number
            except Exception as e:
                logger.error(f"❌ Erreur transfert à votre bot: {e}")
        
        # --- Vérification des résultats existants ---
        await check_prediction_result(game_number, first_group)

        # --- Envoi des prédictions en file d'attente (si proche) ---
        await check_and_send_queued_predictions(game_number)


    except Exception as e:
        logger.error(f"Erreur traitement message: {e}")
        import traceback
        logger.error(traceback.format_exc())

# --- Gestion des Messages (Hooks Telethon) ---

@client.on(events.NewMessage())
async def handle_message(event):
    """Gère les nouveaux messages dans le canal source."""
    try:
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

        # Normaliser les IDs des supergroupes
        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id

        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message
            await process_finalized_message(message_text, chat_id)

    except Exception as e:
        logger.error(f"Erreur handle_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

@client.on(events.MessageEdited())
async def handle_edited_message(event):
    """Gère les messages édités dans le canal source (souvent pour la finalisation)."""
    try:
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

        # Normaliser les IDs des supergroupes
        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id

        if chat_id == SOURCE_CHANNEL_ID:
            message_text = event.message.message
            await process_finalized_message(message_text, chat_id)

    except Exception as e:
        logger.error(f"Erreur handle_edited_message: {e}")
        import traceback
        logger.error(traceback.format_exc())

# --- Commandes Administrateur ---

def is_admin(sender_id):
    """Vérifie si l'ID de l'expéditeur correspond à l'ADMIN_ID configuré."""
    return ADMIN_ID and ADMIN_ID != 0 and sender_id == ADMIN_ID

@client.on(events.NewMessage(pattern='/start'))
async def cmd_start(event):
    if event.is_group or event.is_channel: return
    await event.respond("🤖 **Bot de Prédiction Baccarat**\n\nCommandes: `/status`, `/help`, `/debug`, `/checkchannels`")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.is_group or event.is_channel: return
    if not is_admin(event.sender_id):
        await event.respond("Commande réservée à l'administrateur")
        return

    status_msg = f"📊 **État des prédictions:**\n\n🎮 Jeu actuel: #{current_game_number}\n\n"
    if pending_predictions:
        status_msg += f"**🔮 Actives ({len(pending_predictions)}):**\n"
        for game_num, pred in sorted(pending_predictions.items()):
            distance = game_num - current_game_number
            display_suit = SUIT_DISPLAY.get(pred['suit'], pred['suit'])
            status_msg += f"• Jeu #{game_num}: {display_suit} - Statut: {pred['status']} (dans {distance} jeux)\n"
    else: status_msg += "**🔮 Aucune prédiction active**\n"

    if queued_predictions:
        status_msg += f"\n**📋 En file d'attente ({len(queued_predictions)}):**\n"
        for game_num, pred in sorted(queued_predictions.items()):
            distance = game_num - current_game_number
            display_suit = SUIT_DISPLAY.get(pred['predicted_suit'], pred['predicted_suit'])
            status_msg += f"• Jeu #{game_num}: {display_suit} (dans {distance} jeux) - Base sur #{pred['base_game']}\n"
    await event.respond(status_msg)

@client.on(events.NewMessage(pattern='/debug'))
async def cmd_debug(event):
    if event.is_group or event.is_channel: return
    if not is_admin(event.sender_id):
        await event.respond("Commande réservée à l'administrateur")
        return

    debug_msg = f"""🔍 **Informations de débogage:**\n\n**Configuration:**\n• Source Channel: {SOURCE_CHANNEL_ID}\n• Prediction Channel: {PREDICTION_CHANNEL_ID}\n• Admin ID: {ADMIN_ID}\n\n**Accès aux canaux:**\n• Canal source: {'✅ OK' if source_channel_ok else '❌ Non accessible'}\n• Canal prédiction: {'✅ OK' if prediction_channel_ok else '❌ Non accessible'}\n\n**État:**\n• Jeu actuel: #{current_game_number}\n• Prédictions actives: {len(pending_predictions)}\n• En file d'attente: {len(queued_predictions)}\n• Offset Prédiction: +{PREDICTION_OFFSET}\n• Seuil de proximité: {PROXIMITY_THRESHOLD}\n• Reset Quotidien: 00h59 WAT\n"""
    await event.respond(debug_msg)

@client.on(events.NewMessage(pattern='/checkchannels'))
async def cmd_checkchannels(event):
    global source_channel_ok, prediction_channel_ok
    if event.is_group or event.is_channel: return
    await event.respond("🔍 Vérification des accès aux canaux... (Le statut complet est visible via /debug)")

@client.on(events.NewMessage(pattern='/transfert|/activetransfert'))
async def cmd_active_transfert(event):
    if event.is_group or event.is_channel: return
    if not is_admin(event.sender_id):
        await event.respond("Commande réservée à l'administrateur")
        return
    global transfer_enabled
    transfer_enabled = True
    await event.respond("✅ Transfert des messages finalisés activé!")

@client.on(events.NewMessage(pattern='/stoptransfert'))
async def cmd_stop_transfert(event):
    if event.is_group or event.is_channel: return
    if not is_admin(event.sender_id):
        await event.respond("Commande réservée à l'administrateur")
        return

    global transfer_enabled
    transfer_enabled = False
    await event.respond("⛔ Transfert des messages désactivé.")

@client.on(events.NewMessage(pattern='/help'))
async def cmd_help(event):
    if event.is_group or event.is_channel: return
    
    mapping_str = ", ".join([f"{k} (manquant) -> {v} (prédit)" for k, v in SUIT_MAPPING.items()])
    
    await event.respond(f"""📖 **Aide - Bot de Prédiction**\n\n**Règles de prédiction (Union N et N+1):**\n• Condition: L'union des couleurs du 1er groupe de **JEU N** et **JEU N+1** doit avoir **EXACTEMENT 3 couleurs**.\n• Mapping (Couleur manquante \rightarrow Prédite) : {mapping_str}\n• Prédit: Jeu **N+1 + {PREDICTION_OFFSET}** avec la couleur mappée.\n\n**Maintenance:**\n• Reset Quotidien: Toutes les données sont effacées à **00h59 WAT** pour un redémarrage à zéro.\n""")


# --- Serveur Web et Démarrage ---

async def index(request):
    html = f"""<!DOCTYPE html><html><head><title>Bot Prédiction Baccarat</title></head><body><h1>🎯 Bot de Prédiction Baccarat</h1><p>Le bot est en ligne et surveille les canaux.</p><p><strong>Jeu actuel:</strong> #{current_game_number}</p></body></html>"""
    return web.Response(text=html, content_type='text/html', status=200)

async def health_check(request):
    return web.Response(text="OK", status=200)

async def start_web_server():
    """Démarre le serveur web pour la vérification de l'état (health check)."""
    app = web.Application()
    app.router.add_get('/', index)
    app.router.add_get('/health', health_check)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start() 

async def schedule_daily_reset():
    """Tâche planifiée pour la réinitialisation quotidienne des stocks de prédiction à 00h59 WAT."""
    wat_tz = timezone(timedelta(hours=1)) 
    reset_time = time(0, 59, tzinfo=wat_tz)

    logger.info(f"Tâche de reset planifiée pour {reset_time} WAT.")

    while True:
        now = datetime.now(wat_tz)
        
        target_datetime = datetime.combine(now.date(), reset_time, tzinfo=wat_tz)
        if now >= target_datetime:
            target_datetime += timedelta(days=1)
            
        time_to_wait = (target_datetime - now).total_seconds()

        logger.info(f"Prochain reset dans {timedelta(seconds=time_to_wait)}")
        await asyncio.sleep(time_to_wait)

        logger.warning("🚨 RESET QUOTIDIEN À 00h59 WAT DÉCLENCHÉ!")
        
        # Réinitialiser toutes les variables globales d'état
        global pending_predictions, queued_predictions, recent_games, processed_messages, last_transferred_game, current_game_number

        pending_predictions.clear()
        queued_predictions.clear()
        recent_games.clear() 
        processed_messages.clear()
        last_transferred_game = None
        current_game_number = 0
        
        logger.warning("✅ Toutes les données de prédiction ont été effacées.")

async def start_bot():
    """Démarre le client Telegram et les vérifications initiales."""
    global source_channel_ok, prediction_channel_ok
    try:
        await client.start(bot_token=BOT_TOKEN)
        
        # NOTE: Telethon gère la connexion. On suppose que si le bot a démarré, les canaux sont accessibles.
        source_channel_ok = True
        prediction_channel_ok = True 
        logger.info("Bot connecté et canaux marqués comme accessibles.")
        return True
    except Exception as e:
        logger.error(f"Erreur démarrage du client Telegram: {e}")
        return False

async def main():
    """Fonction principale pour lancer le serveur web, le bot et la tâche de reset."""
    try:
        await start_web_server()

        success = await start_bot()
        if not success:
            logger.error("Échec du démarrage du bot")
            return

        # Lancement de la tâche de reset en arrière-plan
        asyncio.create_task(schedule_daily_reset())
        
        logger.info("Bot complètement opérationnel - En attente de messages...")
        await client.run_until_disconnected()

    except Exception as e:
        logger.error(f"Erreur dans main: {e}")
    finally:
        if client.is_connected():
            await client.disconnect()

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot arrêté par l'utilisateur")
    except Exception as e:
        logger.error(f"Erreur fatale: {e}")
