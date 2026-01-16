# 🚀 Deployment Guide - Polymarket Esports Arbitrage Bot

## Option 1: Railway.app (Recommended - Easiest)

### Steps:
1. **Push to GitHub**
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git remote add origin https://github.com/YOUR_USERNAME/polymarket-bot.git
   git push -u origin main
   ```

2. **Deploy on Railway**
   - Go to [railway.app](https://railway.app)
   - Click "New Project" → "Deploy from GitHub repo"
   - Select your repository
   - Add environment variables (see below)
   - Deploy!

3. **Set Environment Variables in Railway Dashboard:**
   ```
   POLYMARKET_PRIVATE_KEY=your_private_key
   POLYMARKET_API_KEY=your_api_key
   POLYMARKET_API_SECRET=your_api_secret
   POLYMARKET_API_PASSPHRASE=your_passphrase
   PANDASCORE_API_KEY=your_pandascore_key
   PAPER_MODE=false
   INITIAL_CAPITAL=25
   ```

**Cost:** ~$5/month

---

## Option 2: DigitalOcean Droplet

### Steps:
1. **Create Droplet**
   - Go to [digitalocean.com](https://digitalocean.com)
   - Create Droplet → Ubuntu 22.04 → Basic → $4/mo

2. **SSH into server**
   ```bash
   ssh root@YOUR_DROPLET_IP
   ```

3. **Install Docker**
   ```bash
   curl -fsSL https://get.docker.com | sh
   ```

4. **Clone and run**
   ```bash
   git clone https://github.com/YOUR_USERNAME/polymarket-bot.git
   cd polymarket-bot
   
   # Create .env file
   nano .env
   # Add your environment variables
   
   # Run with docker-compose
   docker-compose up -d
   ```

5. **Check logs**
   ```bash
   docker-compose logs -f
   ```

**Cost:** $4-6/month

---

## Option 3: Run on Your Mac 24/7 (Free)

### Using `launchd` (macOS service manager):

1. **Create the service file:**
   ```bash
   cat > ~/Library/LaunchAgents/com.polymarket.bot.plist << 'EOF'
   <?xml version="1.0" encoding="UTF-8"?>
   <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
   <plist version="1.0">
   <dict>
       <key>Label</key>
       <string>com.polymarket.bot</string>
       <key>ProgramArguments</key>
       <array>
           <string>/Users/YOUR_USERNAME/.pyenv/versions/3.11.6/bin/python</string>
           <string>/Users/YOUR_USERNAME/polymarket/main.py</string>
           <string>run</string>
       </array>
       <key>WorkingDirectory</key>
       <string>/Users/YOUR_USERNAME/polymarket</string>
       <key>RunAtLoad</key>
       <true/>
       <key>KeepAlive</key>
       <true/>
       <key>StandardOutPath</key>
       <string>/Users/YOUR_USERNAME/polymarket/logs/bot.log</string>
       <key>StandardErrorPath</key>
       <string>/Users/YOUR_USERNAME/polymarket/logs/bot.error.log</string>
   </dict>
   </plist>
   EOF
   ```

2. **Load the service:**
   ```bash
   mkdir -p ~/polymarket/logs
   launchctl load ~/Library/LaunchAgents/com.polymarket.bot.plist
   ```

3. **Check status:**
   ```bash
   launchctl list | grep polymarket
   tail -f ~/polymarket/logs/bot.log
   ```

4. **Stop/Start:**
   ```bash
   launchctl stop com.polymarket.bot
   launchctl start com.polymarket.bot
   ```

**Cost:** Free (but your Mac needs to stay on)

---

## Option 4: AWS EC2 Free Tier

### Steps:
1. Create AWS account
2. Launch EC2 → t2.micro (free tier)
3. SSH and follow DigitalOcean steps above

**Cost:** Free for 12 months, then ~$8/month

---

## 🔒 Security Notes

1. **Never commit your `.env` file** - it contains private keys!
2. **Use environment variables** in cloud deployments
3. **Start with paper trading** to verify everything works
4. **Monitor your positions** - set up alerts

---

## 📊 Monitoring

### Check bot status:
```bash
# Docker
docker-compose logs -f

# Local
tail -f ~/polymarket/logs/bot.log
```

### Set up Discord/Telegram alerts:
Add webhook URLs to your `.env`:
```
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```
