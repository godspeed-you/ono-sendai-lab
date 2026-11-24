# Moving to a dedicated VM

v0.1.0 implements only the local backend. The design for a dedicated Proxmox VM backend, the portable versus backend-specific state, the transport and authorization prerequisites and the migration runbook are in [ADR-0005](../adr/0005-backend-boundary-and-future-vm.md). In short: destroy local generations, bootstrap the same pinned release on the VM, qualify it with `aws-lab doctor` and the acceptance suite, recreate scenarios. Emulated state is disposable; nothing is migrated live.
