# Migrations of the node database

`server/db.py` applies every `NNNN_name.sql` in this folder that the schema does
not have yet, in name order, when FastAPI starts. It remembers them in the table
`schema_migrations`.

- Never change a file that is merged: add a new one.
- Lower case, digits and underscores only: `0001_saved_cards.sql`.
- No schema names in the SQL. The same files run in every tenant schema later
  (ADR 0011 §4).
