# Local Send

Local Send is a small, LAN-only file sharing app. One Windows laptop runs the server; people on the same Wi-Fi or wired LAN open that laptop's local IP address, join a room code, and download shared files. Files remain on the host laptop and travel only over the local network.

It has no cloud account, external API, or internet requirement while running.

## Start on the host laptop

1. Install Python 3.11+ if it is not already installed.
2. In PowerShell, open this project folder.
3. (Recommended) create a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   py -m pip install -r requirements.txt
   ```

4. Run the server:

   ```powershell
   py app.py
   ```

   The server binds to **`0.0.0.0`** on port **8080**, so it accepts connections from the LAN instead of only from `localhost`.

5. Open `http://localhost:8080` on the host. The terminal and home page will show an address such as `http://192.168.1.25:8080`. Send that address to other people on the same network.

If Windows Defender Firewall prompts you, allow Python on **Private networks**. Do not allow it on public/untrusted networks unless you understand the risk.

## Use it

1. On the host laptop, choose whether uploads are **collaborative** or **owner-only**, then click **Create room**.
2. Share the room code (or copied invite link) with people connected to the same LAN/Wi-Fi.
3. Guests open the host's LAN URL, enter the code, then download listed files.
4. In collaborative rooms, anyone in the room can upload. In owner-only rooms, only the creating device can upload. The creator can change this setting, delete individual files with the trash icon, or delete the room (and all its files) from **Room controls**.

## Notes and troubleshooting

- The laptop must stay awake and connected to the LAN for downloads to work.
- The Wi-Fi network does not need an internet connection. Devices only need to be able to communicate with each other locally.
- If a guest cannot reach the server, check that they used the host's LAN IP (not `localhost`), both devices are on the same network, guest/AP isolation is disabled, and the Windows Firewall rule is allowed on Private networks.
- The default per-file limit is 2048 MB. Before starting, change it with `set LOCAL_SEND_MAX_UPLOAD_MB=4096` in Command Prompt, or `$env:LOCAL_SEND_MAX_UPLOAD_MB=4096` in the current PowerShell session.
- Data and uploaded files are stored locally in `data/`. Anyone with the room code can download, and collaborative rooms also allow uploads, so use it only on trusted LANs and do not treat a room code as strong security.
