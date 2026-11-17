#!/usr/bin/env python3
"""End-to-end local pipeline: upgrade, ML second-sweep reranking & verification.

Fully local: everything runs against the docker-compose stack on this machine
(MariaDB + API + web). Uses only Python stdlib on the host and runs `jobs/*.py`
inside the built server image on the compose network so `mariadb` and `httpx`
are guaranteed present. LLM/embedding calls go to whatever OpenAI-compatible
endpoints are configured in `.env` (LLM_BASE_URL / EMBED_BASE_URL), local or
hosted.
"""
import glob
import json
import os
import py_compile
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

IDEMPOTENT_SCHEMA_SQL = """
USE wikipedia;
SET @@system_versioning_alter_history = KEEP;

ALTER TABLE page_links
  MODIFY source VARCHAR(32) NOT NULL DEFAULT 'wikidata_sitelink',
  ADD COLUMN IF NOT EXISTS confidence FLOAT NOT NULL DEFAULT 1.0 AFTER source,
  ADD COLUMN IF NOT EXISTS evidence JSON NULL AFTER confidence,
  ADD COLUMN IF NOT EXISTS verified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP AFTER evidence;

ALTER TABLE claims
  ADD COLUMN IF NOT EXISTS subject_entity VARCHAR(32) NULL AFTER subject,
  ADD COLUMN IF NOT EXISTS predicate_entity VARCHAR(32) NULL AFTER predicate,
  ADD COLUMN IF NOT EXISTS object_entity VARCHAR(32) NULL AFTER predicate_entity,
  ADD COLUMN IF NOT EXISTS value_canonical JSON NULL AFTER value_normalized,
  ADD COLUMN IF NOT EXISTS qualifiers JSON NULL AFTER value_canonical,
  ADD COLUMN IF NOT EXISTS context_text MEDIUMTEXT NULL AFTER statement_text,
  ADD COLUMN IF NOT EXISTS citation_urls JSON NULL AFTER context_text,
  ADD COLUMN IF NOT EXISTS extraction_confidence FLOAT NULL AFTER citation_urls,
  ADD COLUMN IF NOT EXISTS data_quality_score FLOAT NULL AFTER extraction_confidence,
  ADD COLUMN IF NOT EXISTS salience_score FLOAT NULL AFTER data_quality_score,
  ADD INDEX IF NOT EXISTS idx_claims_predicate (predicate),
  ADD INDEX IF NOT EXISTS idx_claims_predicate_entity (predicate_entity);

ALTER TABLE claim_embeddings
  ADD COLUMN IF NOT EXISTS embedding_quality FLOAT NULL;

ALTER TABLE chunk_embeddings
  ADD COLUMN IF NOT EXISTS embedding_quality FLOAT NULL;

ALTER TABLE claim_links
  ADD COLUMN IF NOT EXISTS relation ENUM('same','related','different','unknown') NOT NULL DEFAULT 'unknown' AFTER similarity,
  ADD COLUMN IF NOT EXISTS candidate_confidence FLOAT NULL AFTER relation,
  ADD COLUMN IF NOT EXISTS verdict_confidence FLOAT NULL AFTER candidate_confidence;

ALTER TABLE claim_links MODIFY verdict
  ENUM('supported','contradicted','possible_match','possible_conflict',
       'unverifiable','definition_mismatch','unmatched','different_fact') NOT NULL;

ALTER TABLE claim_links MODIFY verdict_source
  ENUM('deterministic','llm','human','deterministic-fallback') NOT NULL;

CREATE TABLE IF NOT EXISTS resolutions (
  id            BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
  claim_link_id BIGINT UNSIGNED NOT NULL,
  author        VARCHAR(128) NOT NULL,
  correct_text  TEXT NOT NULL,
  source_url    VARCHAR(2048) NULL,
  created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  KEY idx_res_link (claim_link_id),
  CONSTRAINT fk_res_link FOREIGN KEY (claim_link_id) REFERENCES claim_links (id)
) ENGINE=InnoDB WITH SYSTEM VERSIONING;

ALTER TABLE suggestions
  ADD COLUMN IF NOT EXISTS resolution_id BIGINT UNSIGNED NULL AFTER claim_link_id,
  ADD INDEX IF NOT EXISTS idx_sugg_resolution (resolution_id);

UPDATE claim_links
   SET verdict_source = 'deterministic-fallback'
 WHERE verdict_source = 'llm'
   AND (rationale LIKE 'LLM error:%' OR rationale LIKE 'no LLM configured%');
"""


def load_env(path: str) -> dict:
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def sh(cmd: list[str] | str, *, input_text: str | None = None, env: dict | None = None, check: bool = True) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    p = subprocess.run(
        cmd,
        cwd=ROOT,
        shell=isinstance(cmd, str),
        input=input_text,
        text=True,
        env=merged,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {cmd}\n{p.stdout}")
    return p.stdout


def api_json(base: str, path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=240) as r:
        return json.loads(r.read())


def wait_health(base: str, timeout_s: float = 90.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if api_json(base, "/api/health").get("status") == "ok":
                return
        except Exception:
            pass
        time.sleep(1.5)
    raise RuntimeError(f"Timed out waiting for {base}/api/health")


def step(msg: str):
    print(f"\n=== {msg} ===", flush=True)


def run_job_in_docker(net: str, image: str, dotenv: dict, script_and_args: list[str]) -> str:
    cmd = [
        "docker", "run", "--rm",
        "--network", net,
        "-v", f"{ROOT}/jobs:/jobs:ro",
        "-e", "DATABASE_HOST=mariadb",
        "-e", "DATABASE_PORT=3306",
        "-e", f"DATABASE_USER={dotenv.get('MARIADB_USER', 'ovr')}",
        "-e", f"DATABASE_PASSWORD={dotenv.get('MARIADB_PASSWORD', 'ovr_pass')}",
        "-e", f"DATABASE_NAME={dotenv.get('MARIADB_DATABASE', 'wikipedia')}",
        "-e", f"LLM_API_KEY={dotenv.get('LLM_API_KEY', dotenv.get('OPENAI_API_KEY', ''))}",
        "-e", f"LLM_BASE_URL={dotenv.get('LLM_BASE_URL', dotenv.get('OPENAI_BASE_URL', ''))}",
        "-e", f"LLM_MODEL={dotenv.get('LLM_MODEL', '')}",
        image, "python", *script_and_args,
    ]
    return sh(cmd)


def verify_stack(api_base: str, label: str):
    step(f"Verifying {label} ({api_base})")
    conf = api_json(api_base, "/api/pages/conflicted?limit=15")
    assert conf, f"{label}: /api/pages/conflicted returned empty list"
    mismatches = 0
    for item in conf[:12]:
        pid = item["page"]["id"]
        cmp_view = api_json(api_base, f"/api/pages/{pid}/compare")
        s = cmp_view["summary"]
        got = (item["conflicts"], item["missing_here"], item["only_here"], item["agree"])
        exp = (s["conflict"], s["missing_here"], s["only_here"], s["agree"])
        n_claims_here = sum(len(r.get("here", [])) for r in cmp_view["rows"])
        print(
            f"  [{item['page']['lang']}] {item['page']['title']}: "
            f"landing={got} compare={exp} claims_here={n_claims_here} rows={len(cmp_view['rows'])}",
            flush=True,
        )
        if got != exp:
            mismatches += 1
        for r in cmp_view["rows"]:
            for l in r.get("links", []):
                if str(l.get("rationale") or "").startswith("LLM error:"):
                    raise RuntimeError(f"{label}: found 'LLM error:' rationale on link #{l['id']}!")
    if mismatches:
        raise RuntimeError(f"{label}: found {mismatches} count mismatches between landing and compare!")
    print(f"  OK: {label} verified — 12/12 top pages match 100% and 0 LLM errors!", flush=True)


def main():
    step("0. Syntax-checking all server and jobs Python modules")
    for p in sorted(glob.glob(os.path.join(ROOT, "server", "*.py")) + glob.glob(os.path.join(ROOT, "jobs", "*.py"))):
        py_compile.compile(p, doraise=True)
    print("  All Python modules compiled cleanly.", flush=True)

    dotenv = load_env(os.path.join(ROOT, ".env"))
    db_user = dotenv.get("MARIADB_USER", "ovr")
    db_pass = dotenv.get("MARIADB_PASSWORD", "ovr_pass")
    db_name = dotenv.get("MARIADB_DATABASE", "wikipedia")

    step("1. Stopping hung legacy ovr-extract & applying idempotent schema migrations locally")
    sh("docker rm -f ovr-extract 2>/dev/null || true", check=False)
    sh(["docker", "exec", "-i", "ovr-mariadb", "mariadb", f"-u{db_user}", f"-p{db_pass}", db_name],
       input_text=IDEMPOTENT_SCHEMA_SQL)

    step("2. Rebuilding local server & web containers")
    print(sh(["docker", "compose", "up", "-d", "--build", "server", "web"]), end="", flush=True)
    wait_health("http://127.0.0.1:8000")
    wait_health("http://127.0.0.1:8080")

    net = sh(["docker", "inspect", "-f", "{{range $k,$v := .NetworkSettings.Networks}}{{$k}}{{end}}", "ovr-server"]).strip()
    srv_image = sh(["docker", "inspect", "-f", "{{.Config.Image}}", "ovr-server"]).strip()
    print(f"  Using Docker network={net}, image={srv_image}", flush=True)

    step("3. Healing existing subject-mismatched / fallback links first via regate --all")
    print(sh(["docker", "exec", "-w", "/app", "ovr-server", "python", "regate.py", "--all", "--apply"]), flush=True)

    step("4. Re-extracting bloated Sipilä cabinet & augmenting top 18 conflicted subjects with two-sweep ML claims")
    print(run_job_in_docker(net, srv_image, dotenv, [
        "/jobs/extract_claims.py", "--qids", "Q19876592",
        "--reextract", "--rerank", "--max-claims-per-page", "8", "--concurrency", "2",
    ]), flush=True)
    print(run_job_in_docker(net, srv_image, dotenv, [
        "/jobs/extract_claims.py", "--top-conflicted", "18",
        "--augment", "--rerank", "--max-claims-per-page", "8", "--concurrency", "2",
    ]), flush=True)

    step("5. Embedding all new claims via the configured embeddings endpoint (EMBED_BASE_URL; default ovr-embedder, multilingual-e5-large -> MariaDB VECTOR(1024))")
    print(run_job_in_docker(net, srv_image, dotenv, [
        "/jobs/embed.py", "--endpoint", "http://embedder:8001/v1", "--tables", "claims",
    ]), flush=True)

    step("6. Running Second-Sweep Hybrid ML + MariaDB HNSW Reranker across all LLM-extracted sets (LLM_BASE_URL)")
    print(run_job_in_docker(net, srv_image, dotenv, [
        "/jobs/rerank_claims.py", "--all-llm", "--max-per-page", "8", "--apply",
    ]), flush=True)

    step("7. Aligning & gating all LLM-enriched pages + global error check")
    llm_pages_out = sh(
        ["docker", "exec", "-i", "ovr-mariadb", "mariadb", "-N",
         f"-u{db_user}", f"-p{db_pass}", db_name,
         "-e", "SELECT DISTINCT article_id FROM claims WHERE model_version IS NULL OR model_version <> 'rules-v1' ORDER BY article_id"],
    )
    llm_pids = [int(x.strip()) for x in llm_pages_out.splitlines() if x.strip().isdigit()]
    print(f"Aligning & gating {len(llm_pids)} LLM-enriched pages...", flush=True)
    for pid in llm_pids:
        res = api_json("http://127.0.0.1:8000", f"/api/pages/{pid}/align", method="POST")
        print(f"  aligned page {pid}: claims={res['claims']} new_links={res['candidate_links']} verdicts={res['verdicts']}", flush=True)

    print(sh(["docker", "exec", "-w", "/app", "ovr-server", "python", "regate.py", "--all", "--apply"]), flush=True)

    err_cnt = sh(
        ["docker", "exec", "-i", "ovr-mariadb", "mariadb", "-N",
         f"-u{db_user}", f"-p{db_pass}", db_name,
         "-e", "SELECT COUNT(*) FROM claim_links WHERE rationale LIKE 'LLM error:%'"],
    ).strip()
    if err_cnt != "0":
        sh([
            "docker", "exec", "-w", "/app", "ovr-server", "python", "-c",
            "import verdicts; from db import query; "
            "ids=[r['id'] for r in query(\"SELECT id FROM claim_links WHERE rationale LIKE 'LLM error:%%'\")]; "
            "print('regrading remaining error links:', ids); "
            "print(verdicts.grade_links(verdicts.fetch_pairs_by_ids(ids), touch_ids=set(ids)))"
        ])
    err_cnt_final = sh(
        ["docker", "exec", "-i", "ovr-mariadb", "mariadb", "-N",
         f"-u{db_user}", f"-p{db_pass}", db_name,
         "-e", "SELECT COUNT(*) FROM claim_links WHERE rationale LIKE 'LLM error:%'"],
    ).strip()
    print(f"Total 'LLM error:' links across entire local DB: {err_cnt_final}", flush=True)
    assert err_cnt_final == "0", f"Expected 0 LLM error links, got {err_cnt_final}"

    step("8. Ensuring demo seed votes & verifying the API end-to-end")
    print(sh([sys.executable, "jobs/seed_demo_votes.py", "--apply", "--api", "http://127.0.0.1:8080/api"], check=False), flush=True)
    verify_stack("http://127.0.0.1:8000", "Local API")

    step("9. Final end-to-end verification through the web origin (/api proxy)")
    verify_stack("http://127.0.0.1:8080", "Local Stack (web origin)")
    print("\nALL DONE: the local stack is live, consistent, reranked, and verified!", flush=True)


if __name__ == "__main__":
    main()
