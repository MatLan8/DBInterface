# Distributed Medical Records DB — Summary

## Project goal

Build a demonstrative program that works with a **heterogeneous distributed database**:

- **M1** — relational DBMS (Supabase / PostgreSQL) — financial side of patient care
- **M2** — non-relational DBMS (DataStax Astra / Cassandra) — medical side of patient care

The program must perform **global** data operations across both systems and their **horizontal fragments**, using official drivers/APIs (not only manual admin tools).

Domain: **patient medical records** (visits, payments, clinical data).

---

## Vertical fragmentation (M1 vs M2)

The logical data model is split by concern:

| Side | Store | Role |
|------|--------|------|
| **M1** | Supabase PostgreSQL | Financial / administrative data |
| **M2** | DataStax Cassandra | Medical / clinical data |

Both sides share the logical entity **`Visit`**, linked by:

- shared identifier: **`visit_id`**
- fragmentation attribute: **`region`**

### M1 (PostgreSQL) tables

Per horizontal schema (`db11` / `db12`):

| Table | Purpose |
|-------|---------|
| `patient_profile` | Patient demographics + insurance policy |
| `visit` | Visit financial fields (`patient_id`, payment status, amounts, …) |
| `insurance_claim` | Claims linked to a visit |
| `payment` | Payments linked to a visit |

FK chain: `patient_profile` ← `visit` ← `insurance_claim` / `payment`.

### M2 (Cassandra) tables

Per horizontal keyspace (`db21` / `db22`):

| Table | Purpose |
|-------|---------|
| `doctor` | Doctor profile (license, specialty, clinic) |
| `visit` | Visit clinical fields (doctor, symptoms, diagnosis, nested clinical lists, …) |

Cassandra `visit` stores nested clinical collections as **frozen lists of UDTs**:

- `frozen<list<frozen<medical_procedure_udt>>>` → `procedures`
- `frozen<list<frozen<prescription_udt>>>` → `prescriptions`
- `frozen<list<frozen<lab_result_udt>>>` → `lab_results`

Updates to those collections replace the whole list (Cassandra frozen collection semantics).

### Logical `Visit` (app-level join)

One full visit is reconstructed in the application by joining M1 + M2 on `visit_id` (after routing by `region`):

| Source | Columns |
|--------|---------|
| Postgres `visit` | `visit_id`, `region`, `patient_id`, `payment_status`, `insurance_covered`, `total_amount_eur` |
| Cassandra `visit` | `visit_id`, `region`, `doctor_id`, `visit_date`, `visit_location`, `symptoms`, `diagnosis`, `procedures`, `prescriptions`, `lab_results` |

There is **no** real SQL/CQL cross-DBMS join — the demo program merges results in code.

---

## Horizontal fragmentation (by region)

Criterion: **`region`** ∈ {`Vilnius`, `Kaunas`}.

| Region | M1 (Postgres schema) | M2 (Cassandra keyspace) |
|--------|----------------------|-------------------------|
| Vilnius | `db11` | `db21` |
| Kaunas | `db12` | `db22` |

- `db11` and `db12` have the **same table structure** (only region default / CHECK differ).
- `db21` and `db22` have the **same table structure**.

Postgres enforces region with `CHECK` constraints (e.g. `db11` allows only `'Vilnius'`).  
Cassandra routing relies on the application writing to the correct keyspace.

### Routing rule

```text
region == "Vilnius"  →  Postgres db11  +  Cassandra db21
region == "Kaunas"   →  Postgres db12  +  Cassandra db22
```

Connections in the demo:

- **One** Postgres connection → both schemas (`db11.*`, `db12.*`)
- **One** Astra cluster → two sessions (`db21`, `db22`)

---

## Architecture diagram (logical)

```text
                    ┌─────────────────────────────┐
                    │   Demo program (global ops) │
                    └─────────────┬───────────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
     M1 PostgreSQL (financial)              M2 Cassandra (medical)
              │                                       │
     ┌────────┴────────┐                     ┌────────┴────────┐
     ▼                 ▼                     ▼                 ▼
  db11 Vilnius     db12 Kaunas           db21 Vilnius     db22 Kaunas
  patient_*        patient_*             doctor           doctor
  visit ($)        visit ($)             visit (clinic)   visit (clinic)
  claim/payment    claim/payment
```

---

## Lab task requirements (overview)

From the course assignment (`requirements.txt` / ISDB 2026 lab 2):

### Four global operations (each team member)

1. **Select** — one logical result using data from M1 + M2 and the needed horizontal fragments  
2. **Insert** — create linked data in both sides with a shared ID, into the correct horizontal fragments  
3. **Update** — change a global object; update M1/M2/horizontal fragments as needed (including move if `region` changes)  
4. **Delete** — remove (or mark) the object in both sides without leaving **orphan** records  

### Coordination / failures

- Cross-DBMS ACID / 2PC is **not** required.
- Must implement an **application-level coordination** strategy: operation order, error detection, and at least one recovery approach (compensate, retry, idempotency, outbox, …).

### Extra mandatory items

- Use **at least two Cassandra-specific** API/query features beyond plain CRUD (e.g. partition keys, TTL, frozen UDTs, …).
- **Integrity check** across M1/M2 and horizontal fragments (missing shared IDs, duplicates, wrong fragment, lost rows).
- Demonstrate **one failure scenario** and recovery/compensation.
- Measure **performance** with test data (shown at defense).
- Provide a **repeatable** demo + launch instructions.

### Defense expectations

Run the four global ops, show two DB-specific features, integrity check, failure/recovery, performance result, and explain architecture/code (including how objects move when the fragmentation key changes).

---

## Current implementation status

| Piece | Status |
|-------|--------|
| Connect to M1 + M2 at startup | Done (`main.py`) |
| Horizontal sessions/schemas ready | Done (`db11`/`db12`, `db21`/`db22`) |
| Global **READ** visit by id (`--read VISIT_ID`) | Done — probes `db11` then `db12`, then matching Cassandra keyspace, joins result |
| Global INSERT / UPDATE / DELETE | Not yet |
| Cassandra-specific features | LWT doctor insert and TTL prescription update available |
| Integrity check / failure demo |

### Example

```bash
python main.py --read 1
```

Finds visit in `db11` (Vilnius) → loads medical half from `db21` → prints joined JSON (`financial`, `medical`, `visit`, `routing`).

---

## Design notes for upcoming CRUD

- **Router:** `region` → `{postgres_schema, cassandra_keyspace}`.
- **Shared ID:** same `visit_id` on both vertical halves.
- **Insert/Update/Delete visit:** always coordinate M1 + M2; delete dependents (`payment`, `insurance_claim`) on Postgres to avoid orphans.
- **Region change:** migrate object between horizontal pairs (e.g. `db11+db21` → `db12+db22`) via delete+insert (or equivalent) on both sides.
- **Single-side entities:** `patient_profile` / claims / payments → Postgres only; `doctor` → Cassandra only (placement rule per keyspace still to be fixed in code).
