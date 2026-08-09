# Runtime data migration

Jarvis treats source code and private runtime data as two separate assets. A deployment may replace the Git checkout, but it must never overwrite or delete the persistent data directories.

## Persistent directories

- `JARVIS_DATA_DIR`: task database, recent history, summaries and memory candidates.
- `JARVIS_MEMORY_DIR`: confirmed long-term knowledge and user preferences.
- `JARVIS_WORKSPACE`: uploads, generated files and task workspace.
- Server-side `.env`: credentials and deployment-specific configuration.

These locations must not be committed to Git.

## Safe deployment sequence

1. Keep the current service and server available.
2. Create a timestamped archive of every persistent directory and the server-side environment file.
3. Copy the archive to storage that will survive replacement of the old server.
4. Deploy the new Git checkout into a separate application directory.
5. Configure `JARVIS_DATA_DIR`, `JARVIS_MEMORY_DIR` and `JARVIS_WORKSPACE` to point at restored or existing persistent directories.
6. Start the new service and verify health, memory retrieval, file handling and DingTalk replies.
7. Keep the old service and backup available until acceptance tests pass.
8. Delete the old server only after a restore test confirms the backup is usable.

## Required acceptance checks

- The task database opens without migration errors.
- Recent conversation history is available.
- A known long-term preference can be retrieved from the memory directory.
- A new memory candidate can be created and confirmed.
- DingTalk text, image and file flows still work.
- Generated outputs remain inside the configured workspace.
- The service can be rolled back without modifying the persistent directories.

