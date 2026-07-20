# Deploying Race Data on Render

The permanent Render deployment contains four services from `render.yaml`:

| Service | Type | Purpose |
| --- | --- | --- |
| `race-data-web` | Web service | Public React/Nginx site and same-origin API/WebSocket proxy |
| `race-data-api` | Private service | FastAPI read and admin API on the Render private network |
| `race-data-worker` | Background worker | MongoDB job consumer and FastF1 ingestion |
| `race-data-scheduler` | Background worker | Continuously applies the five-minute/six-hour scheduling policy |

The Blueprint deploys every service in Singapore. Keep all four services in
the same Render workspace and region because the frontend reaches the API over
Render's private network.

## 1. Create MongoDB Atlas

Create a dedicated Atlas cluster in an AWS Singapore region. The complete raw
telemetry archive is much larger than the Atlas free tier, so use a dedicated
cluster with backups and storage auto-expansion enabled.

Create a database user with read/write access to `race_data`, then copy the SRV
connection string. URL-encode special characters in the username or password.
The final value should resemble:

```text
mongodb+srv://race_app:<encoded-password>@<cluster>/race_data?retryWrites=true&w=majority
```

Atlas must allow connections from Render. After the Blueprint creates the
services, open each backend service's **Connect > Outbound** panel and add the
listed Singapore CIDR ranges to the Atlas project IP access list. The first
deploy can fail its readiness check until those ranges are allowed; use
**Manual Deploy > Deploy latest commit** after updating Atlas. A temporary
`0.0.0.0/0` Atlas rule is convenient for initial setup but should be removed as
soon as the Render ranges have been added.

## 2. Deploy the Blueprint

1. Push this repository to GitHub, GitLab or Bitbucket.
2. In Render, select **New > Blueprint** and connect the repository.
3. Confirm that Render finds the root-level `render.yaml`.
4. Enter the requested `MONGODB_URL` and `ADMIN_PASSWORD` secrets.
5. Apply the Blueprint.

Choose the admin password before the API first connects successfully. The API
creates the initial admin record only when that username does not already exist;
changing the environment variable later does not rotate an existing database
password.

The Blueprint automatically passes the web service's public Render hostname to
FastAPI for CORS and the API's private `host:port` to Nginx. The browser always
uses same-origin `/api/v1` and `/api/v1/updates` routes, so admin cookies and
WebSockets remain on the public web hostname. `COOKIE_SECURE=true` is already
set in the Blueprint.

The frontend uses a free web instance by default. The API and continuous
background services require paid instances. The ingestion worker uses a
Standard instance because FastF1 session processing can be memory intensive and
has a 10 GB persistent cache disk at `/cache`.

## 3. Migrate an existing MongoDB database (optional)

Suspend `race-data-worker` and `race-data-scheduler` before importing an
existing database so no writes occur during the migration. With MongoDB
Database Tools installed, run:

```powershell
mongodump --uri "<source-mongodb-url>" --db race_data --archive=race-data.archive
mongorestore --uri "<atlas-srv-url>" --archive=race-data.archive --nsInclude="race_data.*"
```

Check collection counts in Atlas, then resume the two services. Keep the dump
in protected storage until production verification is complete because it can
contain admin account and session data.

## 4. Load the full archive (optional)

The permanent Blueprint deliberately starts with automatic historical and
telemetry backfills disabled. The scheduler and worker will populate and keep
the current season fresh.

For the complete archive, first suspend `race-data-worker` and
`race-data-scheduler`. Create two temporary Render background workers from the
same repository using the backend Dockerfile and Docker context:

| Temporary worker | Docker command |
| --- | --- |
| `race-data-archive` | `python -m app.backfill --start 2000 --end 2026 --until-complete` |
| `race-data-timing` | `python -m app.timing_backfill --start 2018 --end 2026 --until-complete` |

Set `MONGODB_URL`, `MONGODB_DATABASE=race_data`, and
`FASTF1_CACHE=/cache/fastf1` on both. Give each service its own persistent disk
mounted at `/cache`; Render disks cannot be shared between services. Use at
least a Standard instance and disable automatic deploys while the load is
running.

The loaders coordinate through MongoDB and are resumable. Start both: the
timing loader waits for the season index, and the archive loader waits for the
modern timing checkpoint before processing overlapping sessions. Monitor their
logs and the admin Operations page. Completion requires the archive deep audit
to pass.

After completion:

1. Stop and delete the two temporary workers.
2. Delete their cache disks after retaining any diagnostics you need.
3. Resume `race-data-worker` and `race-data-scheduler`.
4. Leave both backfill feature flags disabled unless intentionally scheduling
   more historical work.

## 5. Verify production

Use the public `race-data-web` URL for every browser-facing check:

```text
https://<web-host>/
https://<web-host>/api/v1/health
https://<web-host>/api/v1/ready
https://<web-host>/api/docs
https://<web-host>/admin
```

Verify that:

- `/api/v1/ready` returns HTTP 200 and names the `race_data` MongoDB database.
- Admin login sets a `Secure`, `HttpOnly`, `SameSite=Lax` cookie.
- `/api/v1/updates` establishes a WebSocket with HTTP status 101.
- Scheduler logs show current-season jobs being queued.
- Worker logs show jobs reaching `sync.completed`.
- Atlas alerts cover storage, connections and replication health.
- Render notifications cover failed deploys, restarts, memory and disk usage.

When adding a custom domain, attach it to `race-data-web`. API requests and
WebSockets remain under that same domain through Nginx, so the private API does
not need to be exposed publicly.

## Free-tier alternative

Render private services and background workers are paid. For a demo deployment,
create the API as a second free **Web Service** instead of a private service.
Use the backend Dockerfile/context and the same Uvicorn command described above,
then set these variables on the frontend service:

```env
API_SCHEME=https
API_HOSTPORT=<api-service>.onrender.com
```

Do not include `https://` in `API_HOSTPORT`. Nginx supplies the scheme and keeps
browser requests on the frontend origin. Both free services can sleep after
inactivity, so the first request can be slow. Free web services also do not
replace the continuously running ingestion worker and scheduler; populate
Atlas from an existing database or run those processes elsewhere for a demo.
