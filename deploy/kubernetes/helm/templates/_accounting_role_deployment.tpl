{{- define "deltallm.accountingRoleDeployment" -}}
{{- $role := .values -}}
{{- $configTemplate := .configTemplate -}}
{{- $fullnameTemplate := .fullnameTemplate -}}
{{- $labelsTemplate := .labelsTemplate -}}
{{- $selectorsTemplate := .selectorsTemplate -}}
{{- $component := .component -}}
{{- $processes := .processes -}}
{{- with .root }}
{{- include "deltallm.validateRouterStateSchemaCutover" . -}}
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include $fullnameTemplate . }}
  labels:
    {{- include $labelsTemplate . | nindent 4 }}
spec:
  revisionHistoryLimit: {{ .Values.revisionHistoryLimit }}
  minReadySeconds: {{ .Values.minReadySeconds }}
  replicas: {{ $role.replicaCount }}
  strategy:
    type: {{ .Values.strategy.type }}
    {{- if eq .Values.strategy.type "RollingUpdate" }}
    rollingUpdate:
      maxUnavailable: {{ .Values.strategy.rollingUpdate.maxUnavailable }}
      maxSurge: {{ .Values.strategy.rollingUpdate.maxSurge }}
    {{- end }}
  selector:
    matchLabels:
      {{- include $selectorsTemplate . | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include $selectorsTemplate . | nindent 8 }}
        {{- with $role.podLabels }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      annotations:
        {{- if .Values.dependencyCapacity.extended.enabled }}
        checksum/capacity: {{ include "deltallm.capacityReport" . | sha256sum }}
        {{- end }}
        checksum/config: {{ include $configTemplate . | sha256sum }}
        {{- if .Values.prometheus.podAnnotations.enabled }}
        prometheus.io/scrape: "true"
        prometheus.io/port: {{ .Values.service.port | quote }}
        prometheus.io/path: "/metrics"
        {{- end }}
        {{- with $role.podAnnotations }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
    spec:
      terminationGracePeriodSeconds: {{ .Values.terminationGracePeriodSeconds }}
      serviceAccountName: {{ include "deltallm.serviceAccountName" . }}
      automountServiceAccountToken: {{ .Values.serviceAccount.automountServiceAccountToken }}
      {{- with .Values.image.pullSecrets }}
      imagePullSecrets:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- $priorityClassName := default .Values.priorityClassName $role.priorityClassName }}
      {{- if $priorityClassName }}
      priorityClassName: {{ $priorityClassName }}
      {{- end }}
      securityContext:
        {{- toYaml .Values.podSecurityContext | nindent 8 }}
      {{- $dependencyWaitInitContainers := include "deltallm.dependencyWaitInitContainers" (merge (dict "onlyPostgresql" (eq ((include $configTemplate . | fromYaml).general_settings.accounting_execution_mode) "local_journal")) .) }}
      {{- if $dependencyWaitInitContainers -}}
{{ $dependencyWaitInitContainers | nindent 6 }}
      {{- end }}
      containers:
        - name: {{ $component }}
          image: {{ include "deltallm.image" . | quote }}
          imagePullPolicy: {{ .Values.image.pullPolicy }}
          securityContext:
            {{- toYaml .Values.securityContext | nindent 12 }}
          {{- $command := default .Values.command $role.command }}
          {{- with $command }}
          command:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- $args := default .Values.args $role.args }}
          {{- with $args }}
          args:
            {{- toYaml . | nindent 12 }}
          {{- end }}
          ports:
            - name: http
              containerPort: {{ .Values.service.port }}
              protocol: TCP
          env:
{{ include "deltallm.runtimeEnv" (dict "root" . "extraEnv" $role.env "processes" $processes "configTemplate" $configTemplate) | indent 12 }}
          {{- $minimal := eq ((include $configTemplate . | fromYaml).general_settings.accounting_execution_mode) "local_journal" }}
          {{- $envFrom := include "deltallm.envFrom" (dict "root" . "extraEnvFrom" $role.envFrom "minimal" $minimal) }}
          {{- if $envFrom -}}
{{ $envFrom | nindent 10 }}
          {{- end }}
          volumeMounts:
            {{- if .Values.dependencyCapacity.extended.enabled }}
            - name: capacity
              mountPath: /app/capacity
              readOnly: true
            {{- end }}
            - name: scratch
              mountPath: /tmp
            - name: config
              mountPath: /app/config
              readOnly: true
            {{- with .Values.extraVolumeMounts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
            {{- with $role.extraVolumeMounts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
{{- include "deltallm.probes" . | nindent 10 }}
          resources:
            {{- toYaml $role.resources | nindent 12 }}
      volumes:
        {{- if .Values.dependencyCapacity.extended.enabled }}
        - name: capacity
          configMap:
            name: {{ include "deltallm.fullname" . }}-dependency-capacity
            items:
              - key: report.json
                path: report.json
        {{- end }}
        - name: scratch
          emptyDir:
            sizeLimit: 512Mi
        - name: config
          configMap:
            name: {{ include $fullnameTemplate . }}-config
        {{- with .Values.extraVolumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
        {{- with $role.extraVolumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- $nodeSelector := default .Values.nodeSelector $role.nodeSelector }}
      {{- with $nodeSelector }}
      nodeSelector:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- $affinity := default .Values.affinity $role.affinity }}
      {{- with $affinity }}
      affinity:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- $topology := default .Values.topologySpreadConstraints $role.topologySpreadConstraints }}
      {{- with $topology }}
      topologySpreadConstraints:
        {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- $tolerations := default .Values.tolerations $role.tolerations }}
      {{- with $tolerations }}
      tolerations:
        {{- toYaml . | nindent 8 }}
      {{- end }}
{{- end }}
{{- end -}}
