/**
 * Studio · Коннекторы.
 *
 * The existing /connectors page already does what this module needs —
 * we just re-export it under the new Studio route. The old /connectors
 * route stays live (with an "(старая)" suffix in sidebar) until PR-S9
 * deletes the legacy section, per CONCEPT.md §2.6.
 *
 * If the underlying implementation needs to grow Studio-specific features
 * (e.g. per-module health rollup), we'll fork it then. For now: zero
 * duplicate code.
 */
export { default } from "@/app/connectors/page";
