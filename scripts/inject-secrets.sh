#!/usr/bin/env bash
# Usage: ./scripts/inject-secrets.sh [setup | docker compose args]
# This script retrieves secrets from native OS vaults and injects them into Docker Compose.

set -euo pipefail

SECRET_KEYS=("WHATSAPP_TOKEN" "WHATSAPP_PHONE_NUMBER_ID" "WHATSAPP_VERIFY_TOKEN" "WHATSAPP_API_KEY")
CONFIG_DIR="$HOME/.config/sammurai"
KEYRING_FILE="$CONFIG_DIR/keyring-ids"
SECRETS_FILE=".env.secrets"

detect_os() {
    local os_name
    os_name="$(uname -s)"
    case "$os_name" in
        Darwin*)  echo "macos" ;;
        Linux*)   echo "linux" ;;
        MSYS*|MINGW*|CYGWIN*) echo "windows" ;;
        *)        echo "unknown" ;;
    esac
}

retrieve_secrets_macos() {
    for key in "${SECRET_KEYS[@]}"; do
        local val
        val=$(security find-generic-password -s sammurai -a "$key" -w 2>/dev/null || true)
        if [[ -n "$val" ]]; then
            printf -v "$key" '%s' "$val"
            export "$key"
        fi
    done
}

retrieve_secrets_linux() {
    for key in "${SECRET_KEYS[@]}"; do
        local val=""
        if command -v pass >/dev/null 2>&1; then
            val=$(pass show "sammurai/$key" 2>/dev/null || true)
        fi
        
        if [[ -z "$val" && -f "$KEYRING_FILE" ]]; then
            local id
            id=$(grep "^$key=" "$KEYRING_FILE" | cut -d'=' -f2 | tail -n1)
            if [[ -n "$id" ]]; then
                val=$(keyctl pipe "$id" 2>/dev/null || true)
            fi
        fi

        if [[ -n "$val" ]]; then
            printf -v "$key" '%s' "$val"
            export "$key"
        fi
    done
}

retrieve_secrets_windows() {
    for key in "${SECRET_KEYS[@]}"; do
        local val
        val=$(powershell.exe -NoProfile -Command "
            \$cred = [Windows.Security.Credentials.PasswordVault, Windows.Security.Credentials, ContentType=WinRT]::new().Retrieve('sammurai', '$key')
            if (\$cred) { Write-Output \$cred.Password }
        " 2>/dev/null | tr -d '\r' || true)
        
        if [[ -n "$val" ]]; then
            printf -v "$key" '%s' "$val"
            export "$key"
        fi
    done
}

retrieve_secrets_fallback() {
    if [[ -n "${SECRETS_FILE_PATH:-}" && -f "$SECRETS_FILE_PATH" ]]; then
        local decrypted_content
        if [[ "$SECRETS_FILE_PATH" == *.age ]]; then
            decrypted_content=$(age --decrypt "$SECRETS_FILE_PATH")
        elif [[ "$SECRETS_FILE_PATH" == *.gpg || "$SECRETS_FILE_PATH" == *.asc ]]; then
            decrypted_content=$(gpg --decrypt --quiet "$SECRETS_FILE_PATH")
        else
            return
        fi
        
        while IFS= read -r line; do
            [[ "$line" =~ ^#.*$ || -z "$line" ]] && continue
            if [[ "$line" =~ ^([^=]+)=(.*)$ ]]; then
                local key="${BASH_REMATCH[1]}"
                local val="${BASH_REMATCH[2]}"
                printf -v "$key" '%s' "$val"
                export "$key"
            fi
        done <<< "$decrypted_content"
    fi
}

setup_secrets() {
    local os
    os=$(detect_os)
    echo "Setting up secrets for $os..."
    mkdir -p "$CONFIG_DIR"

    for key in "${SECRET_KEYS[@]}"; do
        read -rs -p "Enter value for $key: " val
        echo
        case "$os" in
            macos)
                security add-generic-password -U -s sammurai -a "$key" -w "$val"
                ;;
            linux)
                if command -v pass >/dev/null 2>&1; then
                    printf "%s" "$val" | pass insert -f "sammurai/$key"
                else
                    local id
                    id=$(printf "%s" "$val" | keyctl padd user "$key" @u)
                    sed -i "/^$key=/d" "$KEYRING_FILE" 2>/dev/null || true
                    echo "$key=$id" >> "$KEYRING_FILE"
                fi
                ;;
            windows)
                powershell.exe -NoProfile -Command "
                    \$vault = [Windows.Security.Credentials.PasswordVault, Windows.Security.Credentials, ContentType=WinRT]::new()
                    \$cred = [Windows.Security.Credentials.PasswordCredential]::new('sammurai', '$key', '$val')
                    \$vault.Add(\$cred)
                "
                ;;
        esac
    done
    echo "Setup complete."
}

main() {
    if [[ "${1:-}" == "setup" ]]; then
        setup_secrets
        exit 0
    fi

    local os
    os=$(detect_os)
    case "$os" in
        macos)   retrieve_secrets_macos ;;
        linux)   retrieve_secrets_linux ;;
        windows) retrieve_secrets_windows ;;
    esac
    retrieve_secrets_fallback

    local found_any=false
    touch "$SECRETS_FILE"
    chmod 600 "$SECRETS_FILE"
    
    for key in "${SECRET_KEYS[@]}"; do
        if [[ -n "${!key:-}" ]]; then
            printf '%s=%q\n' "$key" "${!key}" >> "$SECRETS_FILE"
            found_any=true
        fi
    done

    if [ "$found_any" = false ]; then
        echo "Error: No secrets found in vault or fallback file. Run '$0 setup' first."
        rm -f "$SECRETS_FILE"
        exit 1
    fi

    trap 'rm -f "$SECRETS_FILE"' EXIT
    
    docker compose --env-file "$SECRETS_FILE" up "$@"
}

main "$@"