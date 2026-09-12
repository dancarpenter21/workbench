# Shared contracts

`src/workbench_contracts` is the canonical Pydantic API model package. It has no dependency
on an application or hardware library. Each backend validates incoming and outgoing data
against these models. Errors use `{code, message}`.

From the repository root run `npm run contracts` after changing models or routes. This
exports `openapi.json` for all services and generates `src/api.d.ts` for TypeScript clients.
Both generated artifacts belong in version control. The dashboard uses them with
`openapi-fetch`; service prefixes in the combined spec identify the application.

Build/validate through `uv sync`, `npm run contracts`, and `npm run build` at the root.
