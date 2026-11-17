{{- define "aiops.name" -}}
{{- printf "%s-backend" .Release.Name | trunc 45 | trimSuffix "-" -}}
{{- end -}}
{{- define "aiops.labels" -}}
app.kubernetes.io/name: aiops
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
