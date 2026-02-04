# Security Policy

## Supported Versions

The following versions of DeltaLLM are currently supported with security updates:

| Version | Supported          |
| ------- | ------------------ |
| 0.9.x   | :white_check_mark: |
| < 0.9   | :x:                |

## Reporting a Vulnerability

We take the security of DeltaLLM seriously. If you believe you have found a security vulnerability, please follow these steps:

### ⚠️ Please do NOT

- Open a public GitHub issue for a security vulnerability
- Discuss the vulnerability in public Discord/Slack channels
- Post details on social media or forums

### ✅ Please DO

1. **Email us directly** at everythingjson@gmail.com with:
   - A description of the vulnerability
   - Steps to reproduce the issue
   - Potential impact assessment
   - Any suggested fixes (if you have them)

2. **Allow time for response**: We aim to respond within 48 hours and will work with you to understand and address the issue promptly.

3. **Coordinated disclosure**: We follow responsible disclosure practices. Once the vulnerability is fixed, we will:
   - Credit you in the security advisory (unless you prefer to remain anonymous)
   - Publish a security advisory on GitHub
   - Release a patched version

## Security Best Practices

When deploying DeltaLLM, we recommend the following security measures:

### API Keys
- Store API keys in environment variables or secure secrets management (not in code)
- Rotate keys regularly
- Use different keys for different environments
- Monitor key usage for anomalies

### Database
- Use strong passwords for PostgreSQL
- Enable SSL/TLS for database connections
- Restrict database access to application servers only
- Regular backups with encryption

### Network
- Deploy behind a reverse proxy (nginx/traefik) with HTTPS
- Use VPC/private networks for internal services
- Implement rate limiting at the edge
- Enable CORS only for trusted origins

### Dashboard
- Use strong admin passwords
- Enable 2FA if available
- Regularly audit user access
- Monitor audit logs

### Container Security
- Run containers as non-root user
- Use specific image tags (not `latest`)
- Regularly update base images
- Scan images for vulnerabilities

## Security Features

DeltaLLM includes several built-in security features:

- **RBAC (Role-Based Access Control)**: Granular permissions for organizations and teams
- **API Key Management**: Secure key generation, rotation, and revocation
- **Rate Limiting**: Prevent abuse with RPM/TPM limits
- **Audit Logging**: Track all API calls and administrative actions
- **Budget Enforcement**: Prevent runaway spending with hierarchical budgets
- **PII Detection**: Built-in guardrails for detecting sensitive information

## Known Security Considerations

1. **API Keys in Headers**: DeltaLLM requires API keys to be passed in headers. Ensure HTTPS is always used in production.

2. **Provider API Keys**: Provider API keys are stored encrypted in the database. Encryption key should be kept secure.

3. **Model Access**: By default, all models from configured providers are accessible. Use RBAC to restrict model access.

4. **File Uploads**: If enabling file upload features, validate file types and sizes to prevent abuse.

## Acknowledgments

We thank the following security researchers who have responsibly disclosed vulnerabilities:

*(This list will be updated as vulnerabilities are reported and fixed)*

---

For questions about security, contact: everythingjson@gmail.com
