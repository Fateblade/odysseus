ARG LITELLM_IMAGE
FROM ${LITELLM_IMAGE}
COPY scripts/litellm_audit.py /app/odysseus_audit.py
COPY services/operations/audit.py /app/odysseus_audit_store.py
