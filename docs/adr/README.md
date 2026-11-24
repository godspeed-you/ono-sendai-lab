# Architecture decision records

| ADR | Decision | Requirements |
| --- | --- | --- |
| [0001](0001-dependency-selection-and-provenance.md) | Pinned, signature-verified prebuilt AWS Emulator package; hashed botocore | LAB-010, LAB-011, LAB-012 |
| [0002](0002-containment-and-resource-controls.md) | bubblewrap namespaces inside a transient systemd user unit; disk ledger | LAB-023, LAB-027, LAB-030..032 |
| [0003](0003-lifecycle-process-ownership-and-state.md) | Generations, leases, process ownership, state schema, test channel | LAB-033, LAB-034, LAB-040..043 |
| [0004](0004-managed-client-and-worker-channel.md) | In-namespace managed client and private worker channel | LAB-024..026, LAB-051 |
| [0005](0005-backend-boundary-and-future-vm.md) | Transport-neutral backend contract and VM migration runbook | LAB-003, LAB-020, LAB-021, LAB-083 |
| [0006](0006-host-platform-and-python.md) | Qualified host: Ubuntu 26.04, Python 3.14; 24.04/3.12 coverage | LAB-010, LAB-032 |
| [0007](0007-cli-contract-details.md) | CLI details the spec leaves open | LAB-050..052 |

ADRs resolve ambiguity below the adopted specification. They never change a requirement.
