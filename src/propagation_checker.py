"""Check expected DNS answers across resolver adapters."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import time
from typing import Any


Resolver = Callable[[str, str, str], Iterable[str]]


@dataclass(frozen=True)
class PropagationResult:
	converged: bool
	missing: Mapping[str, tuple[str, ...]]


def _normalize_answer(value: str) -> str:
	return value.strip().lower().rstrip(".")


def check_propagation(
	records: Iterable[Mapping[str, Any]],
	resolvers: Mapping[str, Resolver],
	*,
	attempts: int = 1,
	interval: float = 0,
	wait: Callable[[float], None] = time.sleep,
) -> PropagationResult:
	"""Poll each resolver until it returns all expected values or attempts are exhausted.

	Resolver functions receive ``(name, type, resolver_label)`` and yield answer strings.
	"""
	if attempts < 1 or interval < 0:
		raise ValueError("attempts must be positive and interval must not be negative")
	expected: dict[tuple[str, str], set[str]] = defaultdict(set)
	for record in records:
		expected[(str(record["name"]), str(record["type"]).upper())].add(
			_normalize_answer(str(record["value"]))
		)

	missing: dict[str, tuple[str, ...]] = {}
	for label, resolver in resolvers.items():
		absent: set[str] = set()
		for (name, record_type), values in expected.items():
			for attempt in range(attempts):
				answers = {_normalize_answer(answer) for answer in resolver(name, record_type, label)}
				if values.issubset(answers):
					break
				if attempt + 1 < attempts and interval:
					wait(interval)
			else:
				absent.add(f"{name} {record_type}: {', '.join(sorted(values - answers))}")
		if absent:
			missing[label] = tuple(sorted(absent))
	return PropagationResult(converged=not missing, missing=missing)
