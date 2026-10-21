# Code signing policy

## Current status

SimpleBoard is preparing an application to the SignPath Foundation program. The project has not been approved, and signing integration is pending. The currently published **1.0.7 preview executable is unsigned**. Source availability and checksums do not establish a trusted publisher signature or guarantee that browser reputation warnings will disappear.

## Maintainers and approval

The public source repository is [renbingcheng/simpleboard](https://github.com/renbingcheng/simpleboard).

| Role | Member |
| --- | --- |
| Author / maintainer | [renbingcheng](https://github.com/renbingcheng) |
| Reviewer | [renbingcheng](https://github.com/renbingcheng) |
| Signing approver | [renbingcheng](https://github.com/renbingcheng) |

Before signing is enabled, every team member with repository or signing access must enable multi-factor authentication for both services. Changes proposed by contributors outside the trusted maintainer group must be reviewed by the designated reviewer. Every production signing request must receive manual approval from the designated signing approver.

## Build and signing scope

The intended signing target is the project's `SimpleBoard.exe`, built from reviewed public source on a GitHub-hosted Windows runner. The preparation workflow is intended to run tests, build the unsigned executable, check the frozen application, and retain artifacts with their source commit and build information. It does not submit signing requests and has not been verified by SignPath.

Before production signing, the integration must verify the binary's CI origin and enforce the product name `SimpleBoard` and the matching release version. Third-party components retain their own authorship and licensing and are not separate signing targets under this policy. Signed output must be verified before checksums and download archives are produced, and must not be modified afterward.

## Privacy policy

SimpleBoard currently performs its whiteboard operations offline. It does not implement telemetry, account sign-in, automatic updates, advertising, or automatic uploads. It does not transfer board contents, inserted images, settings, recovery drafts, logs, or input diagnostics to network services.

Settings, logs, and recovery drafts are stored in `%LOCALAPPDATA%\LocalWhiteboard\LocalWhiteboard`. Editable board files and exports are saved to locations chosen by the user. Input diagnostics are disabled by default; when enabled they remain in memory until the user explicitly exports them. Users should inspect any board or diagnostic file before sharing it.

Downloading releases or visiting GitHub, Gitee, or SignPath uses those services and is governed by their respective privacy policies. The application's local operation does not require contacting them.

## Removing the portable application

Close SimpleBoard and delete the downloaded executable or extracted application folder. To remove local settings, logs, and recovery drafts, first save or back up any work you need, then remove `%LOCALAPPDATA%\LocalWhiteboard\LocalWhiteboard`. This removes recovery drafts too. User-saved boards and exports in other folders are managed separately and are not removed automatically.
