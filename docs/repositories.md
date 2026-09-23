# Day 2-B repository boundary

`app.db.repositories` is the database access layer for the first APIs and investigation
services. Construct a repository with the request's SQLAlchemy `Session`. Repository methods
may flush, but never commit or roll back; the request/service owns the transaction.

| Repository | Current operations |
| --- | --- |
| `MeterRepository` | Get a meter, list a stable page |
| `ReadingRepository` | Half-open `[start, end)` meter and transformer windows; idempotent meter-reading insert |
| `EventRepository` | Find or insert by idempotency key; mutable processing status does not change event identity |
| `CaseRepository` | Get/list cases, create a case and de-duplicated affected-meter links |
| `ReportRepository` | List versions, fetch latest, lock the parent case and append a new version while superseding the previous one |
| `HistoryRepository` | Chronological case events and cases previously linked to a meter |
| `TariffRepository` | Find all tariff rows effective for a segment and instant |
| `FinanceRepository` | Get/add one financial impact per report, verifying case/report ownership |
| `RankingRepository` | List active ranked cases; get/add one assessment per report and copy its score, band, and rank to the case |

Reads return ORM objects, not API response models. The upcoming API layer will serialize
them with explicit Pydantic response types and map `RepositoryConflict` and
`RepositoryNotFound` to controlled HTTP errors. Time-window inputs require timezone-aware
datetimes, pages and windows have hard limits, and list ordering is deterministic.

The repository checks existing natural keys for friendly sequential idempotency. The database
unique constraints remain authoritative under concurrency; callers must still roll back and
handle an `IntegrityError` if another transaction wins the same insert. Report numbering
uses a row lock on the case in PostgreSQL, plus the database `(case_id, version)` unique key.

Current ranking reads treat `open`, `investigating`, and `triaged` as active. This policy is
explicitly configurable in `active_cases`; seeded historical `closed` cases do not appear.
Tariff lookup returns all effective rows because Developer A's three bracket rows cannot yet
be collapsed into one flat rate. The finance calculation itself is not part of this layer.
