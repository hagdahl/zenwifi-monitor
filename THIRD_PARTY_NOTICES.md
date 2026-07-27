<!-- ZenWiFi Monitor version: 0.1.0 -->

# Third-party notices

ZenWiFi Monitor does not vendor third-party source code. Its direct Python
dependencies are installed from their published distributions according to
`requirements.txt`. Their upstream license files remain part of those
distributions.

## asusrouter 1.21.3

- Upstream: <https://github.com/Vaskivskyi/asusrouter>
- License: Apache License 2.0
- Copyright notice preserved from the upstream `NOTICE` file:

  `Copyright Yevhenii Vaskivskyi`

The dependency is used for authenticated ASUSWRT-compatible router
communication. If a ZenWiFi Monitor distribution ever vendors, modifies, or
redistributes `asusrouter` code, it must include the complete Apache-2.0
license and the applicable upstream notice files with that distribution.

## keyring 25.6.0

- Upstream: <https://github.com/jaraco/keyring>
- License: MIT License

The dependency provides the Windows Credential Manager backend. Its upstream
license remains part of the installed package distribution.

## Maintenance rule

When a direct dependency is added or its pinned version is changed, update
this file and verify the installed distribution's license metadata and notice
files before publication.
