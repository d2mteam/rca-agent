# Java demo fixture

Build the local fixture image with `bash demo/build.sh`. The multi-stage build runs
the Spring Boot API and database tests before producing `hitl-demo-backend:1.0.0`.
It uses Java 21, Spring Boot/JDBC, Jakarta Validation and Flyway migrations. Flyway
coordinates schema initialization when backend replicas start together.

The agent deploys `examples/webapp/compose.yaml` only after its normal preview and
approval flow. It starts Nginx → two Spring Boot replicas → PostgreSQL. PostgreSQL
has a named volume and an internal network; only Nginx publishes port 8080 on
loopback. Credentials are deliberately local-demo credentials.

API through `http://127.0.0.1:8080`:

- `GET /health`: `{ "status": "UP", "database": "UP", "instance": "hostname" }`;
  queries PostgreSQL and returns HTTP 503 if the connection/query fails.
- `GET /api/instance`: `{ "instance": "hostname" }`, allowing verification that
  every replica actually receives traffic through Nginx.
- `POST /api/records`, JSON `{ "value": "survives-adjust" }`: HTTP 201 with
  `{ "id": 1, "value": "survives-adjust", "createdAt": "..." }`.
- `GET /api/records`: an array of those records ordered by ID.

An adjust from two replicas at 256 MiB to three replicas at 512 MiB should retain
the original records. Nginx resolves Docker DNS every two seconds so added backend
replicas enter the upstream without a handwritten list or Nginx recreation.

The build tests use H2 in PostgreSQL compatibility mode. They validate API,
validation, migration and JDBC behavior; the approval-driven live demo separately
validates real PostgreSQL persistence, Docker health and replica routing.
