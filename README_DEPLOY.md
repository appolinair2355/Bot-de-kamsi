
# 📦 Déploiement sur Render.com

## ✅ Fichiers inclus dans le ZIP
- `main.py` - Code principal du bot
- `config.py` - Configuration
- `requirements.txt` - Dépendances Python
- `render.yaml` - Configuration automatique Render.com

---

## 🚀 Étapes de déploiement

### 1. Créer un compte Render.com
- Allez sur https://render.com
- Inscrivez-vous gratuitement avec GitHub/GitLab/Email

### 2. Uploader le projet
**Option A - GitHub (Recommandé):**
1. Créez un nouveau dépôt GitHub
2. Uploadez tous les fichiers du ZIP
3. Sur Render.com, cliquez "New +" → "Blueprint"
4. Connectez votre dépôt GitHub
5. Render détectera automatiquement `render.yaml`

**Option B - Direct:**
1. Sur Render.com, cliquez "New +" → "Web Service"
2. Connectez votre dépôt ou utilisez "Public Git repository"
3. Configurez manuellement (voir section 3)

### 3. Configurer les variables d'environnement
Dans le dashboard Render, section "Environment", ajoutez:

**⚠️ OBLIGATOIRE:**
- `API_ID` : Votre API ID Telegram (obtenu sur https://my.telegram.org)
- `API_HASH` : Votre API Hash Telegram
- `BOT_TOKEN` : Token de votre bot (@BotFather)
- `ADMIN_ID` : Votre ID Telegram (pour recevoir les messages)

**✅ Déjà configuré (optionnel):**
- `SOURCE_CHANNEL_ID` : -1002682552255 *(Canal Baccarat Kouamé)*
- `PREDICTION_CHANNEL_ID` : -1001626824569 *(Canal de prédiction)*
- `PORT` : 10000 *(Port Render.com)*
- `TELEGRAM_SESSION` : *(Sera généré automatiquement au premier démarrage)*

### 4. Obtenir votre ADMIN_ID
1. Sur Telegram, envoyez `/start` à **@userinfobot**
2. Il vous donnera votre ID numérique (ex: 1190237801)
3. Copiez ce numéro dans la variable `ADMIN_ID`

### 5. Déployer
1. Cliquez sur **"Deploy"** ou **"Create Web Service"**
2. Le bot se lancera automatiquement sur le port 10000
3. Attendez 2-3 minutes pour le premier démarrage

---

## 📱 Commandes disponibles

Une fois le bot déployé, envoyez-lui ces commandes sur Telegram:

- `/start` - Démarrer le bot
- `/transfert` - Activer le transfert des messages finalisés
- `/stoptransfert` - Désactiver le transfert (mode silencieux)
- `/activetransfert` - Réactiver le transfert
- `/status` - Voir les prédictions en cours
- `/debug` - Informations système et configuration
- `/help` - Aide complète

---

## 🔍 Vérifier que le bot fonctionne

### Sur Render.com:
1. Allez dans **"Logs"**
2. Vous devriez voir:
```
✅ Bot Telegram connecté
✅ Bot opérationnel: @VotreBot
✅ Accès au canal source confirmé: Baccarat Kouamé
```

### Sur Telegram:
1. Envoyez `/start` à votre bot
2. Il devrait répondre immédiatement
3. Envoyez `/debug` pour voir la configuration

---

## ⚙️ Fonctionnement du bot

### 🎯 Logique de prédiction:
1. Le bot surveille le canal source (Baccarat Kouamé)
2. **ATTEND** que les messages avec `⏰` soient finalisés (`✅` ou `🔰`)
3. Analyse les jeux ayant **3 cartes** dans le premier groupe
4. Identifie la **couleur manquante** (♠️, ❤️, ♦️ ou ♣️)
5. Envoie une prédiction pour le jeu `actuel + 5`

### 📊 Exemple:
```
Jeu #180: K♥️K♣️5♣️ (3 cartes) → manque ♠️
Jeu #182: J♣️A♦️3♥️ (3 cartes) → manque ♠️
→ Prédiction: Jeu #185 (180+5) en ♠️
```

### ✅ Vérification automatique:
- **✅0️⃣** = Couleur trouvée au numéro prédit → SUCCÈS
- **✅1️⃣** = Couleur trouvée au numéro +1 → SUCCÈS
- **❌** = Échec → Backup automatique envoyé (numéro+5, couleur opposée)

### 📨 Transfert des messages:
- **Activé** (`/transfert`): Tous les messages finalisés sont envoyés à votre bot
- **Désactivé** (`/stoptransfert`): Les messages sont traités en silence, seules les prédictions sont envoyées

---

## 🛠️ Dépannage

### Le bot ne se connecte pas:
- Vérifiez `API_ID`, `API_HASH` et `BOT_TOKEN`
- Assurez-vous que le token est valide (@BotFather)

### Le bot ne reçoit pas les messages:
- Ajoutez le bot comme **membre** du canal source
- Vérifiez que `SOURCE_CHANNEL_ID` est correct

### Les prédictions ne s'envoient pas:
- Ajoutez le bot comme **administrateur** du canal de prédiction
- Vérifiez que `PREDICTION_CHANNEL_ID` est correct

### Voir les logs en direct:
```bash
Sur Render.com → Votre service → Onglet "Logs"
```

---

## 💰 Coûts

**Plan Gratuit Render.com:**
- ✅ 750 heures/mois gratuites
- ✅ Suffisant pour 1 bot 24/7
- ⚠️ Redémarre automatiquement après 15 min d'inactivité
- 💡 Pour éviter cela: Upgrade vers un plan payant ($7/mois)

---

## 🔐 Sécurité

**Ne partagez JAMAIS:**
- Votre `API_HASH`
- Votre `BOT_TOKEN`
- Votre `TELEGRAM_SESSION`

Ces informations donnent un accès complet à votre bot !

---

## 📞 Support

Pour toute question:
1. Vérifiez d'abord les logs Render.com
2. Utilisez la commande `/debug` sur le bot
3. Consultez la documentation Telegram: https://core.telegram.org/bots
