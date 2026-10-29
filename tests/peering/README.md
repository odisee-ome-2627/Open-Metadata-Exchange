# Peering test with two local nodes

Two INN nodes run on one machine and peer with each other. An article posted on `node-a`
must arrive on `node-b`. This tests the INN configuration of this repository (templates,
entrypoint, peers in `config.json`) without the API or the frontend.

```text
          Docker network "peering" (172.30.0.0/24)
 node-a  172.30.0.11  <--- NNTP 119 --->  node-b  172.30.0.12
```

## What is in this folder

| File | Purpose |
| ---- | ------- |
| `compose.yaml` | Builds the image from `Dockerfile-nntp` and starts two nodes with fixed IPs |
| `node-a.json` | Config of node-a: its own name, and node-b as its peer |
| `node-b.json` | Config of node-b: its own name, and node-a as its peer |

Each config is mounted as a Docker secret in `/run/secrets/`, and `OME_CONFIG` tells the
entrypoint which file to read. The passwords are test values only.

## Run the test

Requirements: Docker with Docker Compose v2.

1. Start both nodes from this folder:

   ```shell
   docker compose up -d --build
   docker compose ps
   ```

2. Create the newsgroup on both nodes. A node only stores articles in groups it knows:

   ```shell
   docker compose exec node-a ctlinnd newgroup ome.test
   docker compose exec node-b ctlinnd newgroup ome.test
   ```

3. Open a shell on node-a and post an article:

   ```shell
   docker compose exec node-a bash
   printf "Newsgroups: ome.test\nFrom: test@node-a.invalid\nSubject: Peering test 1\n\nHello from node A\n" | NNTPSERVER=localhost inews -h
   exit
   ```

4. Check that the article arrived on node-b:

   ```shell
   docker compose exec node-b ls /var/spool/news/articles/ome/test
   docker compose exec node-b cat /var/spool/news/articles/ome/test/1
   ```

5. Clean up:

   ```shell
   docker compose down
   ```

## Expected result

The article is stored on node-b, and its `Path` header shows that it came through node-a:

```text
Path: node-b!node-a!.POSTED.localhost!not-for-mail
```

Read `Path` from right to left: posted on node-a, then stored on node-b.

## When it does not work

Look at both sides:

| Question | Where to look |
| -------- | ------------- |
| Did node-a send the article? | `docker compose exec node-a tail -n 20 /var/log/news/innfeed.log` |
| Did node-b refuse it, and why? | `docker compose exec node-b tail -n 20 /var/log/news/news.notice` |
| Does node-a find node-b? | `docker compose exec node-a getent hosts node-b` |
| Does the node read the right config? | `docker compose exec node-a printenv OME_CONFIG` |

Known pitfalls:

- **PowerShell drops the double quotes** inside a command passed to `docker compose exec ... sh -c '...'`.
  Post from a shell inside the container (step 3) instead.
- **`inews` needs a server.** `server` is not set in `inn.conf`, so without `NNTPSERVER=localhost`
  the article is only spooled on disk and not posted.
- **Unique names.** Each node needs its own `hostname` in its config. INN refuses an article
  whose `Path` already contains its own name.
- **Service names.** Nodes find each other by their Compose service name (`node-a`, `node-b`),
  which must match `dns_name` in the configs.

## What this test does not cover

The containers talk directly inside one Docker network. Firewalls, published ports, the real
source IP behind NAT and DNS on the internet are tested on staging, not here.
