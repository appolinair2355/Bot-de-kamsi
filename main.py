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

session_string = os.getenv('TELEGRAM_SESSION', '')
client = TelegramClient(StringSession(session_string), API_ID, API_HASH)

pending_predictions = {}
queued_predictions = {}
recent_games = {}
processed_messages = set()
last_transferred_game = None
current_game_number = 0

MAX_PENDING_PREDICTIONS = 2
PROXIMITY_THRESHOLD = 3

source_channel_ok = False
prediction_channel_ok = False

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
    # On doit avoir EXACTEMENT 1 couleur manquante (donc 3 présentes)
    if len(suits_present) == 3:
        missing = [s for s in ALL_SUITS if s not in suits_present][0]
        return SUIT_DISPLAY.get(missing, missing)
    # Si 2 couleurs présentes = 2 couleurs manquantes → invalide
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

async def send_prediction_to_channel(target_game: int, missing_suit: str, base_game1: int, base_game2: int):
    try:
        alternate_suit = get_alternate_suit(missing_suit)
        backup_game = target_game + 5

        prediction_msg = f"""😼 {target_game}😺: √{missing_suit} statut :🔮"""

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
            'suit': missing_suit,
            'alternate_suit': alternate_suit,
            'backup_game': backup_game,
            'base_game1': base_game1,
            'base_game2': base_game2,
            'status': '🔮',
            'check_count': 0,
            'created_at': datetime.now().isoformat()
        }

        logger.info(f"Prédiction active: Jeu #{target_game} - {missing_suit} (basé sur #{base_game1}+#{base_game2})")
        return msg_id

    except Exception as e:
        logger.error(f"Erreur envoi prédiction: {e}")
        return None

def queue_prediction(target_game: int, missing_suit: str, base_game1: int, base_game2: int):
    if target_game in queued_predictions or target_game in pending_predictions:
        logger.info(f"Prédiction #{target_game} déjà en file ou active, ignorée")
        return False

    queued_predictions[target_game] = {
        'target_game': target_game,
        'missing_suit': missing_suit,
        'base_game1': base_game1,
        'base_game2': base_game2,
        'queued_at': datetime.now().isoformat()
    }
    logger.info(f"📋 Prédiction #{target_game} mise en file d'attente (sera envoyée quand proche)")
    return True

async def check_and_send_queued_predictions(current_game: int):
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

        if distance <= PROXIMITY_THRESHOLD and distance > 0:
            pred_data = queued_predictions.pop(target_game)
            logger.info(f"🎯 Jeu #{current_game} - Prédiction #{target_game} proche ({distance} jeux), envoi maintenant!")

            await send_prediction_to_channel(
                pred_data['target_game'],
                pred_data['missing_suit'],
                pred_data['base_game1'],
                pred_data['base_game2']
            )
        elif distance <= 0:
            logger.warning(f"⚠️ Prédiction #{target_game} expirée (jeu actuel: {current_game}), supprimée")
            queued_predictions.pop(target_game, None)

async def update_prediction_status(game_number: int, new_status: str):
    try:
        if game_number not in pending_predictions:
            return False

        pred = pending_predictions[game_number]
        message_id = pred['message_id']
        suit = pred['suit']

        updated_msg = f"""😼 {game_number}😺: √{suit} statut :{new_status}"""

        if PREDICTION_CHANNEL_ID and PREDICTION_CHANNEL_ID != 0 and message_id > 0 and prediction_channel_ok:
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
    if '⏰' in message:
        return False
    return '✅' in message or '🔰' in message

def analyze_for_prediction(game_number: int, first_group: str):
    """Stocke juste les couleurs du premier groupe pour analyse ultérieure"""
    first_count = count_cards(first_group)

    if first_count >= 2 and first_count <= 3:
        suits_present = get_suits_in_group(first_group)
        logger.info(f"Jeu #{game_number}: {first_count} cartes, couleurs: {suits_present}")
        return {
            'game_number': game_number,
            'suits_in_group': suits_present,
            'first_group': first_group,
            'card_count': first_count
        }
    return None

def check_two_games_sum(game1_data: dict, game2_data: dict):
    """Vérifie si la SOMME des couleurs des 2 jeux a exactement 1 couleur manquante"""
    # Récupérer toutes les couleurs uniques présentes dans les 2 groupes
    all_suits = set(game1_data['suits_in_group']) | set(game2_data['suits_in_group'])
    
    # Calculer les couleurs manquantes
    missing_suits = [s for s in ALL_SUITS if s not in all_suits]
    
    # On veut EXACTEMENT 1 couleur manquante (donc 3 couleurs présentes)
    if len(missing_suits) == 1:
        missing_suit = SUIT_DISPLAY.get(missing_suits[0], missing_suits[0])
        logger.info(f"✅ Jeux #{game1_data['game_number']}+#{game2_data['game_number']}: Couleurs somme: {all_suits}, manquante: {missing_suit}")
        return missing_suit
    else:
        logger.info(f"❌ Jeux #{game1_data['game_number']}+#{game2_data['game_number']}: {len(missing_suits)} couleurs manquantes (besoin de 1)")
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

                backup_target = pred['backup_game']
                alternate_suit = pred['alternate_suit']
                queue_prediction(
                    backup_target,
                    alternate_suit,
                    pred['base_game1'],
                    pred['base_game2']
                )
                logger.info(f"Backup mis en file: #{backup_target} en {alternate_suit}")
                return False

    return None

async def process_finalized_message(message_text: str, chat_id: int):
    global last_transferred_game, current_game_number
    try:
        if not is_message_finalized(message_text):
            return

        game_number = extract_game_number(message_text)
        if game_number is None:
            return

        current_game_number = game_number

        message_hash = f"{game_number}_{message_text[:50]}"
        if message_hash in processed_messages:
            return
        processed_messages.add(message_hash)

        if len(processed_messages) > 200:
            processed_messages.clear()

        groups = extract_parentheses_groups(message_text)
        if len(groups) < 2:
            return

        first_group = groups[0]
        second_group = groups[1]

        logger.info(f"Jeu #{game_number} finalisé (chat_id: {chat_id}) - Groupe1: {first_group}")

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

        await check_and_send_queued_predictions(game_number)

        recent_games[game_number] = {
            'first_group': first_group,
            'second_group': second_group,
            'timestamp': datetime.now().isoformat()
        }

        if len(recent_games) > 100:
            oldest = min(recent_games.keys())
            del recent_games[oldest]

        # Analyse du jeu actuel
        current_analysis = analyze_for_prediction(game_number, first_group)

        if current_analysis:
            # Vérifier avec le jeu PRÉCÉDENT IMMÉDIAT (consécutif)
            prev_game_num = game_number - 1
            if prev_game_num in recent_games:
                prev_game = recent_games[prev_game_num]
                prev_analysis = analyze_for_prediction(prev_game_num, prev_game['first_group'])

                if prev_analysis:
                    # Vérifier la SOMME des 2 jeux
                    missing_suit = check_two_games_sum(prev_analysis, current_analysis)
                    
                    if missing_suit:
                        target_game = game_number + 5
                        if target_game not in pending_predictions and target_game not in queued_predictions:
                            queue_prediction(
                                target_game,
                                missing_suit,
                                prev_game_num,
                                game_number
                            )
                            await check_and_send_queued_predictions(game_number)

    except Exception as e:
        logger.error(f"Erreur traitement message: {e}")
        import traceback
        logger.error(traceback.format_exc())

@client.on(events.NewMessage())
async def handle_message(event):
    try:
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

        if chat_id > 0 and hasattr(chat, 'broadcast') and chat.broadcast:
            chat_id = -1000000000000 - chat_id

        logger.info(f"Message reçu de chat_id={chat_id}, attendu={SOURCE_CHANNEL_ID}")

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
        chat = await event.get_chat()
        chat_id = chat.id if hasattr(chat, 'id') else event.chat_id

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
• `/debug` - Informations de débogage
• `/checkchannels` - Vérifier l'accès aux canaux""")

@client.on(events.NewMessage(pattern='/status'))
async def cmd_status(event):
    if event.is_group or event.is_channel:
        return

    logger.info(f"Commande /status reçue de {event.sender_id}")

    if event.sender_id != ADMIN_ID and ADMIN_ID != 0:
        await event.respond("Commande réservée à l'administrateur")
        return

    status_msg = f"📊 **État des prédictions:**\n\n"
    status_msg += f"🎮 Jeu actuel: #{current_game_number}\n\n"

    if pending_predictions:
        status_msg += f"**🔮 Prédictions actives ({len(pending_predictions)}/{MAX_PENDING_PREDICTIONS}):**\n"
        for game_num, pred in sorted(pending_predictions.items()):
            distance = game_num - current_game_number
            status_msg += f"• Jeu #{game_num}: {pred['suit']} - Statut: {pred['status']} (dans {distance} jeux)\n"
    else:
        status_msg += "**🔮 Aucune prédiction active**\n"

    if queued_predictions:
        status_msg += f"\n**📋 En file d'attente ({len(queued_predictions)}):**\n"
        for game_num, pred in sorted(queued_predictions.items()):
            distance = game_num - current_game_number
            status_msg += f"• Jeu #{game_num}: {pred['missing_suit']} (dans {distance} jeux)\n"

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

**Accès aux canaux:**
• Canal source: {'✅ OK' if source_channel_ok else '❌ Non accessible'}
• Canal prédiction: {'✅ OK' if prediction_channel_ok else '❌ Non accessible'}

**État:**
• Jeu actuel: #{current_game_number}
• Prédictions actives: {len(pending_predictions)}/{MAX_PENDING_PREDICTIONS}
• En file d'attente: {len(queued_predictions)}
• Jeux récents: {len(recent_games)}
• Port: {PORT}

**Règles:**
• Max prédictions simultanées: {MAX_PENDING_PREDICTIONS}
• Seuil de proximité: {PROXIMITY_THRESHOLD} jeux
"""

    await event.respond(debug_msg)

@client.on(events.NewMessage(pattern='/checkchannels'))
async def cmd_checkchannels(event):
    global source_channel_ok, prediction_channel_ok

    if event.is_group or event.is_channel:
        return

    logger.info(f"Commande /checkchannels reçue de {event.sender_id}")

    await event.respond("🔍 Vérification des accès aux canaux...")

    result_msg = "📡 **Résultat de la vérification:**\n\n"

    try:
        source_entity = await client.get_entity(SOURCE_CHANNEL_ID)
        source_title = getattr(source_entity, 'title', 'N/A')
        source_channel_ok = True
        result_msg += f"✅ **Canal source** ({SOURCE_CHANNEL_ID}):\n"
        result_msg += f"   Nom: {source_title}\n"
        result_msg += f"   Statut: Accessible\n\n"
        logger.info(f"✅ Canal source accessible: {source_title}")
    except Exception as e:
        source_channel_ok = False
        result_msg += f"❌ **Canal source** ({SOURCE_CHANNEL_ID}):\n"
        result_msg += f"   Erreur: {str(e)[:100]}\n"
        result_msg += f"   Action: Ajoutez le bot comme membre du canal\n\n"
        logger.error(f"❌ Canal source non accessible: {e}")

    pred_title = "Inconnu"
    try:
        try:
            pred_entity = await client.get_entity(PREDICTION_CHANNEL_ID)
            pred_title = getattr(pred_entity, 'title', 'N/A')
        except Exception as entity_err:
            logger.warning(f"get_entity a échoué, tentative d'envoi direct...")
            try:
                test_msg = await client.send_message(PREDICTION_CHANNEL_ID, "🔍 Test...")
                await asyncio.sleep(1)
                await client.delete_messages(PREDICTION_CHANNEL_ID, test_msg.id)
                pred_entity = await client.get_entity(PREDICTION_CHANNEL_ID)
                pred_title = getattr(pred_entity, 'title', 'N/A')
            except:
                raise entity_err

        try:
            test_msg = await client.send_message(PREDICTION_CHANNEL_ID, "🔍 Test de permissions...")
            await asyncio.sleep(1)
            await client.delete_messages(PREDICTION_CHANNEL_ID, test_msg.id)
            prediction_channel_ok = True
            result_msg += f"✅ **Canal prédiction** ({PREDICTION_CHANNEL_ID}):\n"
            result_msg += f"   Nom: {pred_title}\n"
            result_msg += f"   Statut: Accessible avec droits d'écriture\n\n"
            logger.info(f"✅ Canal prédiction accessible avec droits: {pred_title}")
        except Exception as write_error:
            prediction_channel_ok = False
            result_msg += f"⚠️ **Canal prédiction** ({PREDICTION_CHANNEL_ID}):\n"
            result_msg += f"   Nom: {pred_title}\n"
            result_msg += f"   Erreur écriture: {str(write_error)[:50]}\n"
            result_msg += f"   Action: Le bot doit être ADMINISTRATEUR du canal\n\n"
            logger.warning(f"⚠️ Canal prédiction sans droits d'écriture: {write_error}")
    except Exception as e:
        prediction_channel_ok = False
        me = await client.get_me()
        bot_username = getattr(me, 'username', 'votre_bot')
        result_msg += f"❌ **Canal prédiction** ({PREDICTION_CHANNEL_ID}):\n"
        result_msg += f"   Erreur: {str(e)[:80]}\n"
        result_msg += f"   Action: Ajoutez @{bot_username} comme ADMINISTRATEUR du canal\n\n"
        logger.error(f"❌ Canal prédiction non accessible: {e}")

    if source_channel_ok and prediction_channel_ok:
        result_msg += "🎉 **Tout est prêt!** Le bot peut fonctionner normalement."
    else:
        result_msg += "⚠️ **Actions requises** pour que le bot fonctionne correctement."

    await event.respond(result_msg)

transfer_enabled = True

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

    await event.respond(f"""📖 **Aide - Bot de Prédiction**

**Fonctionnement:**
1. Le bot surveille le canal source
2. Analyse les jeux ayant 2 ou 3 cartes dans le premier groupe
3. Cherche 2 jeux consécutifs avec une couleur manquante
4. Met en file d'attente les prédictions
5. Envoie quand on est à {PROXIMITY_THRESHOLD} jeux du numéro cible
6. Maximum {MAX_PENDING_PREDICTIONS} prédictions actives simultanément

**Commandes:**
• `/start` - Démarrer le bot
• `/status` - Voir les prédictions en cours et en file
• `/checkchannels` - Vérifier l'accès aux canaux
• `/transfert` - Activer transfert des messages
• `/activetransfert` - Réactiver le transfert
• `/stoptransfert` - Désactiver le transfert
• `/debug` - Informations de débogage

**Règles de prédiction:**
• Analyse 2 jeux consécutifs avec 2 ou 3 cartes
• Les DEUX jeux doivent avoir une couleur manquante
• Identifie la couleur manquante (♠️, ❤️, ♦️ ou ♣️)
• Prédit: premier_jeu + 5 avec la couleur manquante
• Envoie quand le jeu actuel est à {PROXIMITY_THRESHOLD} jeux du numéro prédit
• Si échec au numéro ET numéro+1 → Backup automatique

**Exemple:**
Jeu #767: K♥️K♣️ (2 cartes) → manque ♠️ et ♦️
Jeu #769: K♥️K♣️5♣️ (3 cartes) → manque ♠️
→ Prédiction #774 (769+5) mise en file
→ Envoyée quand jeu actuel = #771 (774-{PROXIMITY_THRESHOLD})

**Vérification automatique:**
• ✅0️⃣ = Couleur trouvée au numéro prédit → STOP
• ✅1️⃣ = Couleur trouvée au numéro +1 → STOP
• ❌ = Échec → Backup automatique envoyé""")

async def index(request):
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Prédiction Baccarat</title>
        <meta charset="utf-8">
    </head>
    <body>
        <h1>🎯 Bot de Prédiction Baccarat</h1>
        <p>Le bot est en ligne et surveille les canaux.</p>
        <p><strong>Jeu actuel:</strong> #{current_game_number}</p>
        <p><strong>Prédictions actives:</strong> {len(pending_predictions)}/{MAX_PENDING_PREDICTIONS}</p>
        <p><strong>En file d'attente:</strong> {len(queued_predictions)}</p>
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
        "source_channel_ok": source_channel_ok,
        "prediction_channel": PREDICTION_CHANNEL_ID,
        "prediction_channel_ok": prediction_channel_ok,
        "current_game": current_game_number,
        "pending_predictions": len(pending_predictions),
        "max_pending": MAX_PENDING_PREDICTIONS,
        "queued_predictions": len(queued_predictions),
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
    global source_channel_ok, prediction_channel_ok
    try:
        logger.info("Démarrage du bot...")
        await client.start(bot_token=BOT_TOKEN)
        logger.info("Bot Telegram connecté")

        session = client.session.save()
        logger.info(f"Session Telegram: {session[:50]}... (sauvegardez ceci dans TELEGRAM_SESSION)")

        me = await client.get_me()
        username = getattr(me, 'username', 'Unknown') or f"ID:{getattr(me, 'id', 'Unknown')}"
        logger.info(f"Bot opérationnel: @{username}")

        logger.info("Tentative de découverte des canaux...")

        try:
            source_entity = await client.get_entity(SOURCE_CHANNEL_ID)
            source_channel_ok = True
            logger.info(f"✅ Accès au canal source confirmé: {getattr(source_entity, 'title', 'N/A')}")
        except Exception as e:
            source_channel_ok = False
            logger.error(f"❌ Impossible d'accéder au canal source: {e}")
            logger.error("Le bot doit être ajouté comme membre du canal source!")

        try:
            try:
                pred_entity = await client.get_entity(PREDICTION_CHANNEL_ID)
                logger.info(f"✅ Accès au canal de prédiction: {getattr(pred_entity, 'title', 'N/A')}")
            except Exception as entity_err:
                logger.warning(f"⚠️ get_entity a échoué, tentative d'envoi direct...")
                try:
                    test_msg = await client.send_message(PREDICTION_CHANNEL_ID, "🔍 Test de connexion...")
                    await asyncio.sleep(1)
                    await client.delete_messages(PREDICTION_CHANNEL_ID, test_msg.id)
                    pred_entity = await client.get_entity(PREDICTION_CHANNEL_ID)
                    logger.info(f"✅ Canal découvert via envoi: {getattr(pred_entity, 'title', 'N/A')}")
                except Exception as send_err:
                    raise entity_err

            try:
                test_msg = await client.send_message(PREDICTION_CHANNEL_ID, "🤖 Bot connecté et prêt à envoyer des prédictions!")
                await asyncio.sleep(2)
                await client.delete_messages(PREDICTION_CHANNEL_ID, test_msg.id)
                prediction_channel_ok = True
                logger.info("✅ Permissions d'écriture confirmées dans le canal de prédiction")
            except Exception as write_err:
                prediction_channel_ok = False
                logger.error(f"❌ Pas de droits d'écriture dans le canal de prédiction: {write_err}")
                logger.error("Le bot doit être ADMINISTRATEUR du canal de prédiction!")
        except Exception as e:
            prediction_channel_ok = False
            logger.error(f"❌ Impossible d'accéder au canal de prédiction: {e}")
            logger.error(f"⚠️ Assurez-vous d'ajouter @{username} comme ADMINISTRATEUR du canal de prédiction!")

        logger.info(f"Surveillance du canal source: {SOURCE_CHANNEL_ID}")
        logger.info(f"Envoi des prédictions vers: {PREDICTION_CHANNEL_ID}")
        logger.info(f"📋 Règles: Max {MAX_PENDING_PREDICTIONS} prédictions, envoi à {PROXIMITY_THRESHOLD} jeux de distance")

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