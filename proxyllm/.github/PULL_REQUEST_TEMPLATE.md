## Description
<!-- Provide a brief description of the changes in this PR -->

Fixes # (issue)

## Type of Change
<!-- Mark the relevant option with an [x] -->

- [ ] 🐛 Bug fix (non-breaking change which fixes an issue)
- [ ] ✨ New feature (non-breaking change which adds functionality)
- [ ] 💥 Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] 📚 Documentation update
- [ ] 🔧 Configuration change
- [ ] 🧹 Code refactoring
- [ ] 🧪 Test update
- [ ] 🔌 New provider support

## Changes Made
<!-- List the main changes -->

1. 
2. 
3. 

## Testing
<!-- Describe the tests you ran -->

- [ ] Added unit tests
- [ ] Added integration tests
- [ ] All existing tests pass (`pytest`)
- [ ] Tested manually

### Test Commands
```bash
# Run these commands to verify your changes
pytest tests/unit/test_your_module.py -v
pytest --cov=deltallm --cov-report=html
```

## Provider-Specific Changes (if applicable)
<!-- If this PR adds or modifies a provider, please fill this out -->

- Provider name: 
- [ ] Request transformation implemented
- [ ] Response transformation implemented
- [ ] Streaming support added
- [ ] Error handling implemented
- [ ] Added to provider registry
- [ ] Tests added for the provider

## Checklist
<!-- Mark completed items with [x] -->

- [ ] My code follows the project's style guidelines (ran `ruff check .` and `black .`)
- [ ] I have performed a self-review of my code
- [ ] I have commented my code, particularly in hard-to-understand areas
- [ ] I have made corresponding changes to the documentation
- [ ] My changes generate no new warnings
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing unit tests pass locally with my changes
- [ ] Any dependent changes have been merged and published

## Screenshots (if applicable)
<!-- Add screenshots for UI changes -->

## Additional Notes
<!-- Add any other context about the PR here -->

## Related PRs
<!-- List any related PRs -->

---

**Reviewer Checklist:**
- [ ] Code quality is acceptable
- [ ] Tests are adequate
- [ ] Documentation is updated
- [ ] Changes are backwards compatible (if applicable)
