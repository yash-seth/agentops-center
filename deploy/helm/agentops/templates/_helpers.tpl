{{/* Labels shared by every object. */}}
{{- define "agentops.labels" -}}
app.kubernetes.io/part-of: agentops
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{/* Full image reference for an application component, e.g. include "agentops.image" (dict "root" . "name" "gateway"). */}}
{{- define "agentops.image" -}}
{{ .root.Values.image.registry }}{{ .root.Values.image.prefix }}-{{ .name }}:{{ .root.Values.image.tag }}
{{- end -}}

{{/* Name of the Secret holding credentials. */}}
{{- define "agentops.secretName" -}}
{{- if .Values.secrets.existingSecret -}}{{ .Values.secrets.existingSecret }}{{- else -}}agentops-secrets{{- end -}}
{{- end -}}

{{/* Environment shared by the gateway and console API. POSTGRES_PASSWORD must come first: later
     entries reference it with $(POSTGRES_PASSWORD). */}}
{{- define "agentops.appEnv" -}}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "agentops.secretName" . }}
      key: postgres-password
- name: GOOGLE_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentops.secretName" . }}
      key: google-api-key
      optional: true
- name: GROQ_API_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "agentops.secretName" . }}
      key: groq-api-key
      optional: true
- name: AOC_ENV
  value: {{ .Values.app.env | quote }}
- name: AOC_DB_URL
  value: "postgresql+psycopg://{{ .Values.postgres.user }}:$(POSTGRES_PASSWORD)@postgres:5432/{{ .Values.postgres.database }}"
- name: AOC_PG_DSN
  value: "postgresql://{{ .Values.postgres.user }}:$(POSTGRES_PASSWORD)@postgres:5432/{{ .Values.postgres.database }}"
- name: AOC_VECTOR_STORE
  value: {{ .Values.app.vectorStore | quote }}
- name: AOC_EMBEDDER
  value: {{ .Values.app.embedder | quote }}
- name: AOC_LLM_PROVIDERS
  value: {{ .Values.app.llmProviders | quote }}
- name: AOC_ENABLE_CHAOS_API
  value: {{ .Values.app.enableChaosApi | quote }}
- name: AOC_TRACE_URL_TEMPLATE
  value: {{ .Values.app.traceUrlTemplate | quote }}
{{- if .Values.observability.enabled }}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: "http://otel-collector:4318"
{{- else }}
- name: AOC_TELEMETRY_ENABLED
  value: "false"
{{- end }}
{{- end -}}

{{/* Container security settings for our own images (they run as uid 10001). */}}
{{- define "agentops.securityContext" -}}
runAsNonRoot: true
runAsUser: 10001
allowPrivilegeEscalation: false
capabilities:
  drop: ["ALL"]
{{- end -}}
