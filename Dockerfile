ARG GITEA_IMAGE=gitea/gitea:1.27.2
ARG GITLEAKS_IMAGE=zricethezav/gitleaks:v8.30.0

FROM ${GITLEAKS_IMAGE} AS gitleaks

FROM ${GITEA_IMAGE}

USER root
COPY --from=gitleaks /usr/bin/gitleaks /usr/local/bin/gitleaks
COPY gitea-hooks/pre-receive-gitleaks /usr/local/lib/infralink-gitea-hooks/pre-receive-gitleaks
COPY gitea-hooks/install-receive-gate /usr/local/lib/infralink-gitea-hooks/install-receive-gate
COPY gitea-hooks/entrypoint /usr/local/bin/infralink-gitea-entrypoint
RUN chmod 0755 /usr/local/lib/infralink-gitea-hooks/* \
    /usr/local/bin/infralink-gitea-entrypoint \
    && mkdir -p /usr/local/share/infralink-gitea/git-template/hooks/pre-receive.d \
    && cp /usr/local/lib/infralink-gitea-hooks/pre-receive-gitleaks /usr/local/share/infralink-gitea/git-template/hooks/pre-receive.d/20-infralink-gitleaks \
    && git config --system init.templateDir /usr/local/share/infralink-gitea/git-template
ENTRYPOINT ["/usr/local/bin/infralink-gitea-entrypoint"]
