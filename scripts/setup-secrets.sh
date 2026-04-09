#!/usr/bin/env bash
set -euo pipefail

# Configuration
SERVICE_NAME="sammurai"
SECRETS=("WHATSAPP_API_KEY" "DATABASE_URL" "ENCRYPTION_KEY")
CONFIG_DIR="$HOME/.config/sammurai"
KEYRING_FILE="$CONFIG_DIR/keyring-ids"
WHATSAPP_DATA_DIR="./whatsapp-data"
SECRETS_DIR="./secrets"

# Cleanup trap for temporary files
cleanup() {
    rm -f "$SECRETS_DIR/.tmp_env"
}
trap cleanup EXIT

mkdir -p "$WHATSAPP_DATA_DIR" "$SECRETS_DIR" "$CONFIG_DIR"
chmod 700 "$WHATSAPP_DATA_DIR"

log() { echo -e "\033[1;32m[+]\033[0m $1"; }
warn() { echo -e "\033[1;33m[!]\033[0m $1"; }

check_exists() {
    local key=$1
    case "$(uname -s)" in
        Darwin)
            security find-generic-password -s "$SERVICE_NAME" -a "$key" >/dev/null 2>&1
            ;;
        Linux)
            if command -v pass >/dev/null; then
                pass show "$SERVICE_NAME/$key" >/dev/null 2>&1
            elif command -v keyctl >/dev/null && [ -f "$KEYRING_FILE" ]; then
                grep -q "^${key}:" "$KEYRING_FILE"
            else
                return 1
            fi
            ;;
        *) return 1 ;;
    esac
}

store_secret() {
    local key=$1
    local value=$2
    case "$(uname -s)" in
        Darwin)
            # Use stdin to avoid ps exposure
            echo -n "$value" | security add-generic-password -s "$SERVICE_NAME" -a "$key" -w -U
            ;;
        Linux)
            if command -v pass >/dev/null; then
                echo -n "$value" | pass insert -f -e "$SERVICE_NAME/$key"
            elif command -v keyctl >/dev/null; then
                local kid
                kid=$(echo -n "$value" | keyctl padd user "${SERVICE_NAME}_${key}" @u)
                # Atomic update of keyring file
                local tmp_file
                tmp_file=$(mktemp)
                touch "$KEYRING_FILE"
                grep -v "^${key}:" "$KEYRING_FILE" > "$tmp_file" || true
                echo "${key}:${kid}" >> "$tmp_file"
                mv "$tmp_file" "$KEYRING_FILE"
            else
                return 1
            fi
            ;;
        *) return 1 ;;
    esac
}

fallback_encryption() {
    log "Using fallback encrypted file storage..."
    local tmp_env="$SECRETS_DIR/.tmp_env"
    : > "$tmp_env"
    
    for key in "${SECRETS[@]}"; do
        read -r -s -p "Enter value for $key: " val
        echo ""
        echo "$key=$val" >> "$tmp_env"
    done

    if command -v age >/dev/null; then
        age -p -o "$SECRETS_DIR/sammurai.age" "$tmp_env"
        log "Secrets encrypted to $SECRETS_DIR/sammurai.age"
    else
        gpg --symmetric --cipher-algo AES256 --pinentry-mode loopback -o "$SECRETS_DIR/sammurai.gpg" "$tmp_env"
        log "Secrets encrypted to $SECRETS_DIR/sammurai.gpg"
    fi
    rm -f "$tmp_env"
}

main() {
    local os
    os=$(uname -s)
    
    if [[ "$os" != "Darwin" && "$os" != "Linux" ]]; then
        fallback_encryption
        exit 0
    fi

    # Check if native tools are missing on Linux
    if [[ "$os" == "Linux" ]] && ! command -v pass >/dev/null && ! command -v keyctl >/dev/null; then
        warn "Neither 'pass' nor 'keyctl' found."
        fallback_encryption
        exit 0
    fi

    for key in "${SECRETS[@]}"; do
        if check_exists "$key"; then
            read -r -p "Secret '$key' already exists. Overwrite? (y/N): " confirm
            if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
                continue
            fi
        fi

        read -r -s -p "Enter value for $key: " value
        echo ""
        
        if ! store_secret "$key" "$value"; then
            warn "Failed to store $key natively."
            fallback_encryption
            return
        fi
    done

    log "Setup complete."
    log "Next steps: Run ./scripts/inject-secrets.sh to start the application"
}

main "$@"