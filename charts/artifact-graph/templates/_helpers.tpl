{{- define "ag.name" -}}
{{- printf "%s-graph" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- define "ag.image" -}}
{{- $repo := required "image.repository is required" .Values.image.repository -}}
{{- if .Values.image.digest -}}
{{ printf "%s@%s" $repo .Values.image.digest }}
{{- else -}}
{{ printf "%s:%s" $repo (required "image.tag or image.digest is required" .Values.image.tag) }}
{{- end -}}
{{- end -}}
{{- define "ag.labels" -}}
app.kubernetes.io/name: artifact-graph
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "ag.security" -}}
runAsNonRoot: true
runAsUser: 10001
runAsGroup: 10001
fsGroup: 10001
seccompProfile:
  type: RuntimeDefault
{{- end -}}
{{- define "ag.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities:
  drop: [ALL]
{{- end -}}
