"""Bounded CSV validation. All rows are validated before the first write."""

import csv
import hashlib
import io
from datetime import UTC, datetime

from pydantic import ValidationError as SchemaError

from libs.common.errors import ValidationError
from services.transactions.schemas import TransactionCreate


def parse_csv(text: str, *, account_id: str, currency: str) -> list[TransactionCreate]:
    if len(text.encode("utf-8")) > 262144:
        raise ValidationError("CSV must be at most 256 KiB.")
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")), strict=True)
    required = {"occurred_at", "description", "amount"}
    allowed = required | {"currency", "external_tx_id"}
    rows: list[TransactionCreate] = []
    digest = hashlib.sha256(text.encode()).hexdigest()
    try:
        headers = reader.fieldnames or []
        if (
            not required.issubset(headers)
            or set(headers) - allowed
            or len(headers) != len(set(headers))
        ):
            raise ValidationError(
                "CSV headers: occurred_at,description,amount; optional: currency,external_tx_id."
            )
        for index, row in enumerate(reader, start=2):
            if len(rows) >= 500:
                raise ValidationError("CSV must contain at most 500 transactions.")
            if None in row or any(value is None for value in row.values()):
                raise ValidationError(f"CSV row {index}: incorrect number of columns.")
            stamp = row["occurred_at"]
            # Date-only bank exports mean midnight UTC, explicitly documented.
            if len(stamp) == 10:
                stamp = datetime.strptime(stamp, "%Y-%m-%d").replace(tzinfo=UTC).isoformat()
            try:
                rows.append(
                    TransactionCreate(
                        account_id=account_id,
                        external_tx_id=row.get("external_tx_id") or f"csv:{digest}:{index}",
                        amount=row["amount"],
                        currency=row.get("currency") or currency,
                        description=row["description"],
                        occurred_at=stamp,
                    )
                )
            except SchemaError as exc:
                raise ValidationError(f"CSV row {index}: {exc.errors()[0]['msg']}") from None
    except (csv.Error, ValueError):
        raise ValidationError("CSV contains an invalid date or malformed quoted field.") from None
    if not rows:
        raise ValidationError("CSV has no transaction rows.")
    ids = [row.external_tx_id for row in rows]
    if len(ids) != len(set(ids)):
        raise ValidationError("CSV contains duplicate external_tx_id values.")
    return rows
