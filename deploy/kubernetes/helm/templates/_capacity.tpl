{{- define "deltallm.peakRoleProcesses" -}}
{{- $replicas := int .replicas -}}
{{- if or (lt $replicas 1) (gt $replicas 10000) -}}
{{- fail "Capacity replicas must be between 1 and 10000" -}}
{{- end -}}
{{- $surge := 0 -}}
{{- if eq .strategy.type "RollingUpdate" -}}
{{- $configured := toString .strategy.rollingUpdate.maxSurge -}}
{{- if hasSuffix "%" $configured -}}
{{- if gt (trimSuffix "%" $configured | float64) 100.0 -}}
{{- fail "Capacity maxSurge percentage must be between 0 and 100" -}}
{{- end -}}
{{- $surge = int (ceil (divf (mulf $replicas (trimSuffix "%" $configured | float64)) 100)) -}}
{{- else -}}
{{- if gt (float64 $configured) 10000.0 -}}{{- fail "Capacity maxSurge must not exceed 10000" -}}{{- end -}}
{{- $surge = int $configured -}}
{{- end -}}

{{- end -}}
{{- mul (add $replicas $surge (mul $replicas .retiringGenerations)) .processes -}}
{{- end -}}

{{- define "deltallm.capacityProduct" -}}
{{- $value := 1.0 -}}
{{- range . -}}
{{- $value = mulf $value . -}}
{{- if or (lt $value 0.0) (gt $value 1000000000000.0) -}}
{{- fail "Capacity calculation exceeds its bounded integer range" -}}
{{- end -}}
{{- end -}}
{{- $value | int64 -}}
{{- end -}}
