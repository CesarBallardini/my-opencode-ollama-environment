Feature: the installer changes only what is missing and the uninstaller reverts only that

  These scenarios run the real install.py and uninstall.py entry points against a fake laptop:
  a temporary profile root, a simulated winget, uv, Ollama and firewall, no network.

  Scenario: a laptop that already has Claude Code keeps it
    Given Claude Code is installed outside the local-llm directory
    And the configuration asks for the cloud harnesses
    When I run the installer
    Then the installer succeeds
    And the installer does not install Claude Code
    And the journal has no entry for Claude Code
    When I run the uninstaller with --yes
    Then Claude Code is still installed

  Scenario: a laptop that already has Ollama keeps it
    Given Ollama is already installed
    When I run the installer
    Then the installer succeeds
    And the installer does not install Ollama
    When I run the uninstaller with --yes
    Then Ollama is still installed

  Scenario: what the user had before the install comes back after the uninstall
    Given my own lines in .bashrc
    And my own value for a variable the installer sets
    And my own key in the Ollama server.json
    And my own deny list in the local-llm directory
    When I run the installer
    Then the installer succeeds
    And the installer has set the variable and the server.json key
    And my deny list is unchanged
    When I run the uninstaller with --yes
    Then my .bashrc, my variable and my server.json are as they were
    And my deny list is unchanged
    And what the installer added to the local-llm directory is gone
    And the journal keeps only the Python the uninstaller runs on

  Scenario: a second run changes nothing
    When I run the installer
    And I take a snapshot of the machine
    And I run the installer
    Then the installer succeeds
    And the machine is unchanged since the snapshot
    And the journal is unchanged since the snapshot

  Scenario: a dry run changes nothing
    When I take a snapshot of the machine
    And I run the installer with --dry-run
    Then the installer succeeds
    And the machine is unchanged since the snapshot
    And the journal is empty

  Scenario: an interrupted install resumes without recording a change twice
    Given the first download will fail
    When I run the installer
    Then the installer reports an error mentioning "SHA-256"
    When I run the installer
    Then the installer succeeds
    And no change is recorded twice

  Scenario: a pinned binary is replaced only when asked, and put back on uninstall
    Given another opencode binary is in place
    When I run the installer
    Then the installer succeeds
    And the opencode binary is unchanged
    When I run the installer with --replace-binaries
    Then the opencode binary is the pinned one
    When I run the uninstaller with --yes
    Then the other opencode binary is back

  Scenario: a blocked pull points at ollama-pull.cmd
    Given Ollama is already installed
    And the Ollama server rule exists
    When I run the installer
    Then the installer reports an error mentioning "ollama-pull.cmd"

  Scenario: the secret reaches the credentials file and never the log
    Given a secrets file with an Anthropic key
    And the configuration asks for the cloud harnesses
    When I run the installer
    Then the installer succeeds
    And the key is in the OpenCode credentials
    And the key is not in the log
