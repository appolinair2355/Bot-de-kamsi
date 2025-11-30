"""
Configuration du bot Telegram de prédiction Baccarat
"""
import os

def parse_channel_id(env_var: str, default: str) -> int:
    """
    Récupère un ID de canal depuis une variable d'environnement ou une valeur par défaut.
    Convertit les IDs positifs longs en format Telethon (négatif long, ex: -100xxxxxxxxxx).
    """
    value = os.getenv(env_var) or default
    
    # Si l'ID est déjà au format Telethon (négatif), on le retourne.
    if value.startswith('-100'):
        return int(value)
    
    # Sinon, on tente de le convertir au format Telethon
    try:
        channel_id = int(value)
        # Si c'est un ID positif long (format API), on le convertit
        if channel_id > 0 and len(str(channel_id)) >= 10:
            return int(f"-100{channel_id}") 
        return channel_id
    except ValueError:
        return 0

# --- Identifiants de Canaux ---
# Les ID sont basés sur ceux que vous avez fournis, au format Telethon (négatif)
SOURCE_CHANNEL_ID = parse_channel_id('SOURCE_CHANNEL_ID', '-1002682552255')

PREDICTION_CHANNEL_ID = parse_channel_id('PREDICTION_CHANNEL_ID', '-1002338377421')

# --- Clés d'API et Admin ---
# 🚨 CORRECTION : Remplacer '0' par un placeholder pour forcer la mise à jour
ADMIN_ID = int(os.getenv('ADMIN_ID') or 'VOTRE_ADMIN_ID_REEL') 

# 🚨 CORRECTION : Remplacer '0' par un placeholder pour forcer la mise à jour
API_ID = int(os.getenv('API_ID') or 'VOTRE_API_ID_REEL')

# 🚨 CORRECTION : Remplacer la chaîne vide ('') par un placeholder
API_HASH = os.getenv('API_HASH') or 'VOTRE_API_HASH_REEL' 

# 🚨 CORRECTION : Remplacer la chaîne vide ('') par un placeholder
BOT_TOKEN = os.getenv('BOT_TOKEN') or 'VOTRE_BOT_TOKEN_REEL' 

PORT = int(os.getenv('PORT') or '5000')  # Port 5000 for Replit

# --- Mapping des Couleurs pour la Règle de Prédiction ---
# Logique: {Couleur Manquante: Couleur Prédite}
SUIT_MAPPING = {
    '♠': '♦',  # Si Pique manque, prédire Carreau
    '♦': '♠',  # Si Carreau manque, prédire Pique
    '♣': '♥',  # Si Trèfle manque, prédire Coeur
    '♥': '♣',  # Si Coeur manque, prédire Trèfle
}

# --- Définitions de Couleurs ---
ALL_SUITS = ['♥', '♠', '♦', '♣']

# Mapping pour l'affichage des couleurs
SUIT_DISPLAY = {
    '♠': '♠️',
    '♥': '♥️',
    '♦': '♦️',
    '♣': '♣️'
        }
