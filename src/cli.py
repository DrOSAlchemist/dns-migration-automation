"""Command-line interface for validating, planning, applying, and checking DNS migrations."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import re
import sys
from pathlib import Path
from typing import Any, Sequence

from src.config import load_environment
from src.dns_resolver import make_resolver
from src.ipam_lookup import lookup_ip, parse_networks
from src.propagation_checker import check_propagation
from src.providers.factory import build_ipam_adapter, build_provider
from src.record_validator import validate_records
from src.rollback import load_snapshot, save_snapshot
from src.zone_diff import ZoneDiff, diff_zone
from src.zone_import import load_zone


def _add_environment_arguments(parser: argparse.ArgumentParser) -> None:
	parser.add_argument("--config", default="config/environments.yaml", help="environment YAML path")
	parser.add_argument("--environment", required=True, help="environment name in the configuration")


def _parser() -> argparse.ArgumentParser:
	parser = argparse.ArgumentParser(prog="dns-migrate", description="Plan and operate DNS zone migrations")
	subcommands = parser.add_subparsers(dest="command", required=True)

	validate = subcommands.add_parser("validate", help="validate a JSON zone file")
	validate.add_argument("zone_file")

	for name, help_text in (
		("diff", "compare a zone file with the configured target"),
		("apply", "snapshot and reconcile a zone file to the configured target"),
	):
		command = subcommands.add_parser(name, help=help_text)
		_add_environment_arguments(command)
		command.add_argument("zone_file")
		if name == "apply":
			command.add_argument("--snapshot-dir", default=".snapshots")
			command.add_argument("--confirm", action="store_true", help="required to perform provider writes")

	rollback_command = subcommands.add_parser("rollback", help="restore a saved zone snapshot")
	_add_environment_arguments(rollback_command)
	rollback_command.add_argument("snapshot")
	rollback_command.add_argument("--snapshot-dir", default=".snapshots")
	rollback_command.add_argument("--confirm", action="store_true", help="required to perform provider writes")

	check = subcommands.add_parser("check", help="check zone answers across DNS resolvers")
	check.add_argument("zone_file")
	check.add_argument("--resolver", action="append", dest="resolvers", help="nameserver IP; repeatable")
	check.add_argument("--attempts", type=int, default=5)
	check.add_argument("--interval", type=float, default=5)
	check.add_argument("--timeout", type=float, default=3)

	ipam = subcommands.add_parser("ipam-lookup", help="find the most-specific IPAM network for an address")
	ipam.add_argument("address")
	ipam.add_argument("--inventory", help="JSON file containing a list of IPAM network objects")
	ipam.add_argument("--config", default="config/environments.yaml")
	ipam.add_argument("--environment", help="environment with an 'ipam' TCPWave configuration")
	return parser


def _environment(args: argparse.Namespace) -> dict[str, Any]:
	return load_environment(args.config, args.environment)


def _zone_file(path: str, configured_zone: str | None = None) -> tuple[str, list[dict[str, Any]]]:
	zone, records = load_zone(path)
	if configured_zone and zone.rstrip(".").lower() != configured_zone.rstrip(".").lower():
		raise ValueError(f"zone file contains {zone!r}, but environment targets {configured_zone!r}")
	return zone, records


def _diff_document(changes: ZoneDiff) -> dict[str, Any]:
	return {
		"added": [record_set.as_records() for record_set in changes.added],
		"removed": [record_set.as_records() for record_set in changes.removed],
		"changed": [
			{"before": before.as_records(), "after": after.as_records()}
			for before, after in changes.changed
		],
	}


def _snapshot_path(directory: str, environment: str, zone: str) -> Path:
	environment_slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", environment).strip("-")
	slug = re.sub(r"[^a-zA-Z0-9.-]+", "-", zone.rstrip(".")).strip("-")
	timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
	return Path(directory) / f"{environment_slug}-{slug}-{timestamp}.json"


def _apply(args: argparse.Namespace) -> int:
	if not args.confirm:
		raise ValueError("apply requires --confirm; use 'diff' to review without writing")
	settings = _environment(args)
	zone, desired = _zone_file(args.zone_file, settings.get("zone"))
	provider = build_provider(settings)
	current = provider.list_records(zone)
	changes = diff_zone(current, desired)
	print(json.dumps(_diff_document(changes), indent=2))
	if changes.is_empty:
		print("No changes required.")
		return 0
	snapshot = _snapshot_path(args.snapshot_dir, args.environment, zone)
	save_snapshot(snapshot, zone, current)
	print(f"Snapshot: {snapshot}")
	try:
		provider.apply_diff(zone, changes)
	except Exception as apply_error:
		try:
			provider.replace_zone(zone, current)
		except Exception as rollback_error:
			raise RuntimeError(
				f"apply failed ({apply_error}); automatic rollback also failed ({rollback_error}); "
			f"restore from {snapshot} after investigating provider state"
			) from apply_error
		raise RuntimeError(f"apply failed ({apply_error}); automatic rollback from {snapshot} completed") from apply_error
	print(f"Applied {len(desired)} records to {zone}.")
	return 0


def _rollback(args: argparse.Namespace) -> int:
	if not args.confirm:
		raise ValueError("rollback requires --confirm")
	settings = _environment(args)
	provider = build_provider(settings)
	zone, records = load_snapshot(args.snapshot)
	if settings.get("zone") and zone.rstrip(".").lower() != str(settings["zone"]).rstrip(".").lower():
		raise ValueError(f"snapshot zone {zone!r} does not match configured zone {settings['zone']!r}")
	current = provider.list_records(zone)
	checkpoint = _snapshot_path(args.snapshot_dir, args.environment, zone)
	save_snapshot(checkpoint, zone, current)
	print(f"Pre-rollback snapshot: {checkpoint}")
	provider.replace_zone(zone, records)
	print(f"Restored {len(records)} records to {zone}.")
	return 0


def _check(args: argparse.Namespace) -> int:
	zone, records = _zone_file(args.zone_file)
	validated = validate_records(zone, records)
	resolvers = args.resolvers or ["1.1.1.1", "8.8.8.8"]
	result = check_propagation(
		validated,
		{address: make_resolver(address, args.timeout) for address in resolvers},
		attempts=args.attempts,
		interval=args.interval,
	)
	print(json.dumps({"zone": zone, "converged": result.converged, "missing": result.missing}, indent=2))
	return 0 if result.converged else 1


def _ipam_lookup(args: argparse.Namespace) -> int:
	if args.inventory:
		with Path(args.inventory).open(encoding="utf-8") as inventory_file:
			payload = json.load(inventory_file)
		items = payload.get("networks") if isinstance(payload, dict) else payload
		if not isinstance(items, list):
			raise ValueError("IPAM inventory must be a network array or an object containing 'networks'")
		match = lookup_ip(args.address, parse_networks(items))
	else:
		if not args.environment:
			raise ValueError("provide --inventory or --environment for configured TCPWave IPAM")
		settings = _environment(args)
		ipam_config = settings.get("ipam")
		if not isinstance(ipam_config, dict):
			raise ValueError(f"environment {args.environment!r} has no 'ipam' configuration")
		match = build_ipam_adapter(ipam_config).lookup(args.address)
	print(json.dumps(None if match is None else {
		"network": str(match.network),
		"metadata": dict(match.metadata),
	}, indent=2))
	return 0


def main(argv: Sequence[str] | None = None) -> int:
	args = _parser().parse_args(argv)
	try:
		if args.command == "validate":
			zone, records = _zone_file(args.zone_file)
			print(json.dumps({"zone": zone, "valid": True, "record_count": len(records)}, indent=2))
			return 0
		if args.command == "diff":
			settings = _environment(args)
			zone, desired = _zone_file(args.zone_file, settings.get("zone"))
			changes = diff_zone(build_provider(settings).list_records(zone), desired)
			print(json.dumps(_diff_document(changes), indent=2))
			return 0
		if args.command == "apply":
			return _apply(args)
		if args.command == "rollback":
			return _rollback(args)
		if args.command == "check":
			return _check(args)
		if args.command == "ipam-lookup":
			return _ipam_lookup(args)
		raise ValueError(f"unsupported command: {args.command}")
	except (OSError, ValueError, RuntimeError, NotImplementedError, ImportError, KeyError) as error:
		print(f"error: {error}", file=sys.stderr)
		return 2


if __name__ == "__main__":
	sys.exit(main())