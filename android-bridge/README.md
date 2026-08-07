# Juggluco Headless Android Bridge

This microservice acts as a bridge between the Gluco Track web application and the Abbott LibreView servers. It does this by running a headless Android 11 system (ReDroid), running the Juggluco app, and using an automated UI script to type in your insulin doses and carbohydrates as you log them.

## VPS Requirements

This bridge **MUST** run on a dedicated Linux VPS (like DigitalOcean, Hetzner, Linode). It cannot run on PaaS providers like Railway, Heroku, or Render.

**Hardware**: 1 CPU, 1GB RAM minimum (2GB recommended).
**OS**: Ubuntu 22.04 or Debian 11/12.

## Installation Instructions

1. SSH into your VPS as `root`.
2. Install Docker and Docker Compose if you haven't already:
   ```bash
   apt-get update
   apt-get install -y docker.io docker-compose-v2 linux-modules-extra-$(uname -r)
   ```
3. Load the required Android kernel modules:
   ```bash
   modprobe ashmem_linux
   modprobe binder_linux devices="binder,hwbinder,vndbinder"
   ```
   *(Note: To make this persistent across reboots, add these to `/etc/modules`)*
4. Clone your repository to the VPS and navigate to the `android-bridge` folder:
   ```bash
   git clone https://github.com/marshy-land/gluco-track.git
   cd gluco-track/android-bridge
   ```
5. Create a `.env` file in the `android-bridge` folder with your Railway PostgreSQL database URL:
   ```bash
   echo "DATABASE_URL=postgresql://postgres:password@your-railway-url.railway.app:5432/railway" > .env
   ```
6. Start the Bridge!
   ```bash
   docker compose up -d
   ```

## Initial Juggluco Setup

You need to install the Juggluco APK into the ReDroid container and log in to your LibreView account *once*.

1. While the container is running, download the Juggluco APK (e.g. `Juggluco.apk`) to your VPS.
2. Connect to the container via ADB and install it:
   ```bash
   apt-get install -y adb
   adb connect localhost:5555
   adb install Juggluco.apk
   ```
3. To configure your LibreView account in Juggluco, the easiest way is to use `scrcpy` (Screen Copy) from your local computer to remotely view and control the headless Android screen over the internet! 
   - Forward port 5555 from your VPS to your local computer (e.g., via SSH tunnel).
   - Run `scrcpy` locally, open the Juggluco app, agree to the terms, and log in to your LibreView account.
   - Once logged in, you can close `scrcpy`. The Python worker will handle everything else automatically in the background!
