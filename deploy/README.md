# BEP Upstox on a cloud server (AWS Lightsail, Mumbai)

This guide sets up the app once on a small cloud server. The server runs the app all day, every day. You open it from your PC or phone through **Tailscale**, a private network: the app is never open to the internet, and it asks for your password.

**Do the setup outside market hours.** Step 6 (registering the IP in Upstox) logs your Upstox token out.

## 1. Create the server
1. Go to the AWS Lightsail console → **Create instance**.
2. Choose:
   - Region **Mumbai (ap-south-1)**;
   - Platform **Linux/Unix**, blueprint **OS Only → Ubuntu 24.04 LTS**;
   - the **1 GB RAM** plan.
3. Name it `bep-upstox` and create it.
4. **Networking → Create static IP**, then attach it to `bep-upstox`. This is the IP you register in Upstox. It's free while it's attached.
5. Leave the Lightsail firewall as it is (SSH 22). Don't open port 8084.

## 2. Connect with SSH
Use the **Connect using SSH** button in Lightsail, or from your PC with the downloaded key:

```bash
ssh -i LightsailDefaultKey-ap-south-1.pem ubuntu@<static-ip>
```

## 3. Give the server read access to the repo (deploy key)
On the server:

```bash
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
```

```bash
cat ~/.ssh/id_ed25519.pub
```

On GitHub, open the BEP_Upstox repo → **Settings → Deploy keys → Add deploy key**. Paste the key, leave **Allow write access** off, and save.

## 4. Clone and set up
```bash
git clone git@github.com:ArunkumarEkambaram/BEP_Upstox.git
```

```bash
cd BEP_Upstox && bash deploy/setup_server.sh
```

The script:
- installs Python 3.14 and the app's packages;
- adds the `bep-upstox` service (it starts at boot and restarts within 5 s if it stops);
- sets the IST clock;
- turns on a firewall that allows only SSH and Tailscale;
- installs **Tailscale**.

When it shows a Tailscale login link, open it and log in. If Tailscale asks you to turn on **HTTPS certificates** for your network, follow its link (Tailscale admin console → DNS → enable MagicDNS and HTTPS). Then run this again:

```bash
sudo tailscale serve --bg 8084
```

## 5. Set the app password
```bash
.venv/bin/python auth.py set-password
```

```bash
sudo systemctl restart bep-upstox
```

The password needs at least 8 characters. Each browser stays logged in for 30 days; **Log out** is the icon next to the light/dark button. After 5 wrong tries, login is blocked for 5 minutes.

## 6. Register the static IP in Upstox (after market hours)
1. Go to the Upstox developer site → **My Apps** → your app → **Static IPs**.
2. Enter the Lightsail static IP as the primary IP and save.
3. Saving logs your token out: generate a new one, and enter it on Setup the next morning as usual.

A third-party guide says the IP can be changed only about once a week, so copy it carefully.

## 7. Open the app on your PC and phone
1. Install the Tailscale app on your PC and phone and log in with the same account.
2. Open the address that `sudo tailscale serve status` shows, e.g. `https://bep-upstox.<your-tailnet>.ts.net`.

The app on your PC (BEP-AlgoDelta, port 8083) keeps working as before.

## Daily use
The app is already running. Before 09:08, open it and:
1. enter today's Upstox token on **Setup**;
2. upload the BEP CSV;
3. choose the order mode (Dry run / Sandbox / Live).

## Updating after a merged PR
```bash
cd ~/BEP_Upstox && bash deploy/update.sh
```

It pulls `master`, installs the packages, restarts the app and shows the last log lines. During market hours (Mon–Fri 09:00–15:35) it refuses unless you add `--force`; open positions resume from the orders file after the restart.

## Useful commands
| What | Command |
|---|---|
| Live log | `journalctl -u bep-upstox -f` |
| Status | `systemctl status bep-upstox` |
| Restart | `sudo systemctl restart bep-upstox` |
| Stop / start | `sudo systemctl stop bep-upstox` / `sudo systemctl start bep-upstox` |
| Turn the login off | `.venv/bin/python auth.py remove-password` then restart |
| Server time | `timedatectl` (should be Asia/Kolkata) |

The orders, settings and token files stay in `~/BEP_Upstox`, the same as on your PC. Download the orders from **Order Details** as usual.
