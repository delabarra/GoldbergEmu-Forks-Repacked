import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UPSTREAMS = {
    "detanup": {
        "repo": "Detanup01/gbe_fork",
        "asset_name": "emu-win-release.7z",
        "manifest": "manifests/detanup-keep.txt",
        "name_prefix": "Detanup01",
        "asset_suffix": "win",
    },
    "alex": {
        "repo": "alex47exe/gse_fork",
        "asset_name": "emu-win-release.7z",
        "manifest": "manifests/alex-keep.txt",
        "name_prefix": "alex47exe",
        "asset_suffix": "win",
    },
}

COLDCLIENT_LOADER = {
    "upstream_key": "detanup",
    "manifest": "manifests/detanup-coldclientloader-keep.txt",
    "name_prefix": "ColdClientLoader",
    "asset_suffix": "win",
}

UPSTREAM_LABELS = {
    "detanup": "Detanup01",
    "alex": "alex47exe",
}

SOURCE_META_KEYS = {
    "detanup": ("Source-Detanup-Release-ID", "Source-Detanup-Asset-ID"),
    "alex": ("Source-Alex-Release-ID", "Source-Alex-Asset-ID"),
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    sys.exit(1)


def env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def normalize_tag(tag: str) -> str:
    cleaned = re.sub(r"^release-", "", tag, flags=re.IGNORECASE)
    cleaned = re.sub(r"[^A-Za-z0-9._-]", "-", cleaned)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or "unknown"


def gh_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gbe-repack-bot",
    }


def request_json(url: str, token: str) -> dict:
    req = urllib.request.Request(url, headers=gh_headers(token), method="GET")
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}
        raise


def download_file(url: str, destination: Path, token: str) -> None:
    req = urllib.request.Request(url, headers=gh_headers(token), method="GET")
    with urllib.request.urlopen(req) as response:
        destination.write_bytes(response.read())


def parse_keep_manifest(path: Path) -> list[str]:
    if not path.exists():
        fail(f"Missing keep manifest: {path}")

    entries: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.replace("\\", "/")
        line = line.lstrip("./").lstrip("/")
        if line:
            entries.append(line)

    if not entries:
        fail(f"Keep manifest has no entries: {path}")

    return entries


def resolve_safe(root: Path, rel_path: str) -> Path:
    candidate = (root / rel_path).resolve()
    if root.resolve() not in [candidate, *candidate.parents]:
        fail(f"Unsafe path in manifest: {rel_path}")
    return candidate


def copy_whitelist(src_root: Path, dst_root: Path, keep_paths: list[str]) -> None:
    copied_any = False
    for rel in keep_paths:
        source = resolve_safe(src_root, rel)
        if not source.exists():
            fail(f"Manifest path does not exist in archive: {rel}")

        target = resolve_safe(dst_root, rel)
        if source.is_dir():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, dirs_exist_ok=True)
            copied_any = True
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied_any = True

    if not copied_any:
        fail("No files copied from whitelist")


def seven_zip_extract(archive_path: Path, output_dir: Path) -> None:
    subprocess.run(["7z", "x", "-y", f"-o{output_dir}", str(archive_path)], check=True)


def seven_zip_pack(input_dir: Path, output_archive: Path) -> None:
    if output_archive.exists():
        output_archive.unlink()
    subprocess.run(["7z", "a", "-t7z", str(output_archive), "."], cwd=str(input_dir), check=True)


def get_upstream_release_info(token: str, repo: str, asset_name: str) -> dict:
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    data = request_json(api_url, token)
    if not data:
        fail(f"No latest release found for {repo}")

    assets = data.get("assets", [])
    asset = next((a for a in assets if a.get("name") == asset_name), None)
    if not asset:
        fail(f"Release in {repo} does not contain asset '{asset_name}'")

    return {
        "repo": repo,
        "release_id": data.get("id"),
        "tag": data.get("tag_name", ""),
        "normalized_tag": normalize_tag(data.get("tag_name", "")),
        "release_name": data.get("name", "") or "",
        "html_url": data.get("html_url", "") or f"https://github.com/{repo}/releases/latest",
        "body": data.get("body", "") or "",
        "asset_name": asset_name,
        "asset_id": asset.get("id"),
        "asset_updated_at": asset.get("updated_at", ""),
        "asset_download_url": asset.get("browser_download_url", ""),
    }


def get_latest_own_release(token: str, repo: str) -> dict:
    api_url = f"https://api.github.com/repos/{repo}/releases/latest"
    data = request_json(api_url, token)
    return data or {}


def asset_name(prefix: str, tag: str, suffix: str) -> str:
    return f"{prefix}-{tag}-{suffix}.7z"


def find_asset_by_prefix(assets: list[dict], prefix: str, asset_suffix: str = "win") -> dict | None:
    pattern = re.compile(rf"^{re.escape(prefix)}-(.+)-{re.escape(asset_suffix)}\.7z$")
    for asset in assets:
        name = asset.get("name", "")
        if pattern.match(name):
            return asset
    return None


def extract_tag_from_name(name: str, prefix: str, asset_suffix: str = "win") -> str:
    pattern = re.compile(rf"^{re.escape(prefix)}-(.+)-{re.escape(asset_suffix)}\.7z$")
    match = pattern.match(name)
    return match.group(1) if match else ""


def normalize_upstream_body(body: str) -> str:
    if not body or not body.strip():
        return "_No release notes published upstream._"
    return body.replace("\r\n", "\n").replace("\r", "\n").strip()


def format_fork_section(
    key: str,
    info: dict,
    rebuilt: bool,
) -> str:
    label = UPSTREAM_LABELS.get(key, key)
    repo = info["repo"]
    tag = info["tag"]
    url = info["html_url"]
    title = info.get("release_name", "").strip()
    repo_url = f"https://github.com/{repo}"

    lines = [
        f"## {label}",
        f"Upstream: [{repo}]({repo_url}) · release [`{tag}`]({url})",
    ]
    if title and title != tag:
        lines.append(f"_{title}_")
    lines.append("")
    if rebuilt:
        lines.append(normalize_upstream_body(info.get("body", "")))
    else:
        lines.append(
            f"_Unchanged in this repack — bundled from "
            f"[{label} `{tag}`]({url}). See that release for upstream notes._"
        )
    return "\n".join(lines)


def build_release_body(
    upstream_info: dict[str, dict],
    changed: dict[str, bool],
) -> str:
    detanup_section = format_fork_section(
        "detanup",
        upstream_info["detanup"],
        changed["detanup"],
    )
    alex_section = format_fork_section(
        "alex",
        upstream_info["alex"],
        changed["alex"],
    )

    meta_lines = [
        "<!--",
        f"{SOURCE_META_KEYS['detanup'][0]}: {upstream_info['detanup']['release_id']}",
        f"{SOURCE_META_KEYS['detanup'][1]}: {upstream_info['detanup']['asset_id']}",
        f"{SOURCE_META_KEYS['alex'][0]}: {upstream_info['alex']['release_id']}",
        f"{SOURCE_META_KEYS['alex'][1]}: {upstream_info['alex']['asset_id']}",
        "-->",
    ]

    return "\n\n---\n\n".join([detanup_section, alex_section]) + "\n\n" + "\n".join(meta_lines)


def parse_source_meta(release_body: str) -> dict[str, str]:
    meta: dict[str, str] = {}
    patterns = {
        "detanup_release_id": r"^Source-Detanup-Release-ID:\s*(\S+)\s*$",
        "detanup_asset_id": r"^Source-Detanup-Asset-ID:\s*(\S+)\s*$",
        "alex_release_id": r"^Source-Alex-Release-ID:\s*(\S+)\s*$",
        "alex_asset_id": r"^Source-Alex-Asset-ID:\s*(\S+)\s*$",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, release_body, flags=re.MULTILINE)
        if match:
            meta[key] = match.group(1)

    return meta


def pack_filtered(
    extracted_path: Path,
    output_dir: Path,
    workspace: Path,
    cfg: dict,
    upstream_info: dict,
) -> Path:
    filtered_path = extracted_path.parent / f"filtered-{cfg['asset_suffix']}"
    if filtered_path.exists():
        shutil.rmtree(filtered_path)
    filtered_path.mkdir(parents=True, exist_ok=True)

    keep_manifest = workspace / cfg["manifest"]
    keep_paths = parse_keep_manifest(keep_manifest)
    copy_whitelist(extracted_path, filtered_path, keep_paths)

    prefix = cfg["name_prefix"]
    suffix = cfg["asset_suffix"]
    new_name = asset_name(prefix, upstream_info["normalized_tag"], suffix)
    out_path = output_dir / new_name
    seven_zip_pack(filtered_path, out_path)
    return out_path


def rebuild_from_upstream(
    workspace: Path,
    output_dir: Path,
    upstream_info: dict,
    cfg: dict,
    token: str,
    extracted_path: Path | None = None,
) -> Path:
    if extracted_path is None:
        with tempfile.TemporaryDirectory() as temp_dir_str:
            temp_dir = Path(temp_dir_str)
            archive_path = temp_dir / upstream_info["asset_name"]
            extracted_path = temp_dir / "extracted"
            extracted_path.mkdir(parents=True, exist_ok=True)

            download_file(upstream_info["asset_download_url"], archive_path, token)
            seven_zip_extract(archive_path, extracted_path)
            return pack_filtered(extracted_path, output_dir, workspace, cfg, upstream_info)

    return pack_filtered(extracted_path, output_dir, workspace, cfg, upstream_info)


def carry_forward_asset(
    output_dir: Path,
    existing_asset: dict,
    label: str,
    token: str,
) -> Path:
    existing_name = existing_asset.get("name", "")
    if not existing_name:
        fail(f"Missing existing asset info for {label}")
    existing_url = existing_asset.get("browser_download_url", "")
    if not existing_url:
        fail(f"Missing download url for {existing_name}")

    print(f"{label}: unchanged, carrying forward {existing_name}")
    out_path = output_dir / existing_name
    download_file(existing_url, out_path, token)
    return out_path


def write_output(name: str, value: str) -> None:
    output_file = os.environ.get("GITHUB_OUTPUT")
    if not output_file:
        print(f"{name}={value}")
        return

    with open(output_file, "a", encoding="utf-8") as f:
        if "\n" in value:
            f.write(f"{name}<<EOF\n{value}\nEOF\n")
        else:
            f.write(f"{name}={value}\n")


def load_context(token: str, own_repo: str) -> tuple[dict[str, dict], dict, list[dict], dict[str, str]]:
    upstream_info = {
        key: get_upstream_release_info(token, cfg["repo"], cfg["asset_name"])
        for key, cfg in UPSTREAMS.items()
    }

    latest_own = get_latest_own_release(token, own_repo)
    own_assets = latest_own.get("assets", []) if latest_own else []
    own_meta = parse_source_meta(latest_own.get("body", "") if latest_own else "")

    return upstream_info, latest_own, own_assets, own_meta


def detect_upstream_changes(
    upstream_info: dict[str, dict],
    own_assets: list[dict],
    own_meta: dict[str, str],
) -> dict[str, bool]:
    upstream_changed = {"detanup": False, "alex": False}

    for key, cfg in UPSTREAMS.items():
        prefix = cfg["name_prefix"]
        suffix = cfg["asset_suffix"]
        new_tag = upstream_info[key]["normalized_tag"]

        existing_asset = find_asset_by_prefix(own_assets, prefix, suffix)
        release_meta_key = f"{key}_release_id"
        asset_meta_key = f"{key}_asset_id"
        upstream_release_id = str(upstream_info[key].get("release_id", ""))
        upstream_asset_id = str(upstream_info[key].get("asset_id", ""))

        # Prefer metadata when available. It detects upstream refreshes even if tag names stay unchanged.
        if own_meta.get(asset_meta_key):
            upstream_changed[key] = own_meta[asset_meta_key] != upstream_asset_id
        elif own_meta.get(release_meta_key):
            upstream_changed[key] = own_meta[release_meta_key] != upstream_release_id
        elif existing_asset:
            current_tag = extract_tag_from_name(existing_asset.get("name", ""), prefix, suffix)
            upstream_changed[key] = current_tag != new_tag
        else:
            upstream_changed[key] = True

    return upstream_changed


def build_repacks(
    token: str,
    workspace: Path,
    output_dir: Path,
    upstream_info: dict[str, dict],
    own_assets: list[dict],
    upstream_changed: dict[str, bool],
    force_repack: bool,
) -> dict[str, Path]:
    planned_assets: dict[str, Path] = {}

    for key, cfg in UPSTREAMS.items():
        prefix = cfg["name_prefix"]
        suffix = cfg["asset_suffix"]
        new_tag = upstream_info[key]["normalized_tag"]
        new_name = asset_name(prefix, new_tag, suffix)

        existing_asset = find_asset_by_prefix(own_assets, prefix, suffix)
        changed = upstream_changed[key] or force_repack

        if changed:
            print(f"{key}: upstream changed, rebuilding {new_name}")
            if key == "detanup":
                with tempfile.TemporaryDirectory() as temp_dir_str:
                    temp_dir = Path(temp_dir_str)
                    archive_path = temp_dir / upstream_info[key]["asset_name"]
                    extracted_path = temp_dir / "extracted"
                    extracted_path.mkdir(parents=True, exist_ok=True)
                    download_file(upstream_info[key]["asset_download_url"], archive_path, token)
                    seven_zip_extract(archive_path, extracted_path)
                    planned_assets[key] = pack_filtered(
                        extracted_path, output_dir, workspace, cfg, upstream_info[key]
                    )
                    cc_cfg = COLDCLIENT_LOADER
                    cc_name = asset_name(
                        cc_cfg["name_prefix"],
                        upstream_info[key]["normalized_tag"],
                        cc_cfg["asset_suffix"],
                    )
                    print(f"ColdClientLoader: rebuilding {cc_name}")
                    planned_assets["coldclientloader"] = pack_filtered(
                        extracted_path,
                        output_dir,
                        workspace,
                        cc_cfg,
                        upstream_info[key],
                    )
            else:
                planned_assets[key] = rebuild_from_upstream(
                    workspace, output_dir, upstream_info[key], cfg, token
                )
        else:
            planned_assets[key] = carry_forward_asset(
                output_dir, existing_asset, key, token
            )

    # ColdClientLoader (Detanup01 only) when detanup was not rebuilt above
    if "coldclientloader" not in planned_assets:
        cc_cfg = COLDCLIENT_LOADER
        cc_prefix = cc_cfg["name_prefix"]
        cc_suffix = cc_cfg["asset_suffix"]
        cc_tag = upstream_info[cc_cfg["upstream_key"]]["normalized_tag"]
        cc_new_name = asset_name(cc_prefix, cc_tag, cc_suffix)
        cc_existing = find_asset_by_prefix(own_assets, cc_prefix, cc_suffix)

        if cc_existing:
            planned_assets["coldclientloader"] = carry_forward_asset(
                output_dir, cc_existing, "ColdClientLoader", token
            )
        else:
            print(f"ColdClientLoader: first publish, building {cc_new_name}")
            planned_assets["coldclientloader"] = rebuild_from_upstream(
                workspace,
                output_dir,
                upstream_info[cc_cfg["upstream_key"]],
                cc_cfg,
                token,
            )

    return planned_assets


def cmd_check(token: str, own_repo: str, force_repack: bool) -> None:
    upstream_info, _, own_assets, own_meta = load_context(token, own_repo)
    upstream_changed = detect_upstream_changes(upstream_info, own_assets, own_meta)

    if force_repack:
        print("FORCE_REPACK enabled: will rebuild all assets from latest upstream.")

    if not force_repack and not any(upstream_changed.values()):
        print("No upstream updates detected. Skipping release.")
        write_output("should_release", "false")
        return

    for key, changed in upstream_changed.items():
        label = UPSTREAM_LABELS.get(key, key)
        tag = upstream_info[key]["tag"]
        if changed:
            print(f"{label}: update detected ({tag})")
        else:
            print(f"{label}: unchanged ({tag})")

    write_output("should_release", "true")


def cmd_repack(token: str, own_repo: str, force_repack: bool) -> None:
    workspace = Path(os.getcwd())
    output_dir = workspace / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    upstream_info, _, own_assets, own_meta = load_context(token, own_repo)
    upstream_changed = detect_upstream_changes(upstream_info, own_assets, own_meta)

    if force_repack:
        print("FORCE_REPACK enabled: rebuilding all assets from latest upstream.")

    planned_assets = build_repacks(
        token, workspace, output_dir, upstream_info, own_assets, upstream_changed, force_repack
    )

    detanup_tag = upstream_info["detanup"]["normalized_tag"]
    alex_tag = upstream_info["alex"]["normalized_tag"]

    release_tag = f"repack-{detanup_tag}-{alex_tag}-{os.environ.get('GITHUB_RUN_NUMBER', 'manual')}"
    release_name = f"Detanup01 {detanup_tag} | alex47exe {alex_tag}"
    changelog_changed = {
        key: upstream_changed[key] or force_repack for key in ("detanup", "alex")
    }
    release_body = build_release_body(upstream_info, changelog_changed)

    write_output("release_tag", release_tag)
    write_output("release_name", release_name)
    write_output("release_body", release_body)
    write_output("asset_one_path", str(planned_assets["detanup"]))
    write_output("asset_two_path", str(planned_assets["alex"]))
    write_output("asset_three_path", str(planned_assets["coldclientloader"]))


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor upstream releases and build repacks.")
    parser.add_argument(
        "command",
        choices=("check", "repack"),
        help="check: detect upstream updates; repack: build assets and prepare release outputs",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    own_repo = os.environ.get("GITHUB_REPOSITORY")

    if not token:
        fail("GITHUB_TOKEN is required")
    if not own_repo:
        fail("GITHUB_REPOSITORY is required")

    force_repack = env_bool("FORCE_REPACK")

    if args.command == "check":
        cmd_check(token, own_repo, force_repack)
    else:
        cmd_repack(token, own_repo, force_repack)


if __name__ == "__main__":
    main()
