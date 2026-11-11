# Security model and limitations

FaceUnlock is experimental software for an already signed-in Windows 11 session. A normal RGB camera plus a gesture is not equivalent to Windows Hello hardware-backed face authentication. Replayed video, deepfakes, accidental gestures and lookalikes are relevant risks. No measured false-accept rate is claimed; cosine similarity is not a confidence percentage.

The camera agent does not read a Windows password. A native credential provider accepts a short-lived named-pipe signal only from the configured installed executable path. That executable, models and original enrollment are installed under Program Files; the trusted path is stored under HKLM. PIN and password providers are not filtered.

The account password is stored in LSA private data and passed to Windows by the credential provider. Administrator access, offline access or code injection into the agent may defeat these controls. A changed account password must be updated by running the installer again. This implementation supports one configured Windows account per machine.

Settings, logs and adaptive samples are user-writable. Invalid settings pause recognition; selected gestures are checked in both recognition and the unlock decision. Original enrollment requires administrator-mediated refresh. Adaptive samples are filtered against the original enrollment and never lower its calibrated threshold. An attacker who can edit settings can disable automatic locking; this is not an enterprise enforcement mechanism.

When the camera is unavailable, covered, or temporarily held by the preview, automatic locking pauses. This prioritizes avoiding unexpected locks over enforcing a lock in every uncertain situation. Windows' own timeout policy can remain enabled separately.

No camera frames, face vectors or credentials are uploaded by the application. Initial setup downloads Python dependencies and public model files. Model downloads are SHA-256 checked. The repository and release package must never contain enrollment arrays, local JSON state, logs or secrets; `.gitignore` excludes these artifacts.

Before sharing a release, validate installation, reboot, lock/unlock, camera failure and removal on a separate Windows 11 device with an available PIN/password recovery path. Automated tests do not validate the full LogonUI lifecycle. Release binaries are not code signed.

For a security report, omit personal biometric data and passwords. Use the repository owner's private reporting channel when one is configured; do not post credentials in a public issue.
