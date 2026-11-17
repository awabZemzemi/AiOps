{{/* Leave room for component suffixes within Kubernetes DNS label limits. */}}
{{- define "sentinelshop.name" -}}
{{- .Release.Name | trunc 45 | trimSuffix "-" -}}
{{- end -}}
