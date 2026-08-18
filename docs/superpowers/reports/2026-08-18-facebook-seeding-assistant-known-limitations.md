# Facebook Seeding Assistant — Known Limitations

- Facebook DOM selectors are deliberately conservative. If the page contains multiple plausible articles/composers/submit controls, the extension stops for review instead of choosing one heuristically.
- The MVP does not discover target posts or groups. Every target must already exist in the ACP queue.
- The MVP does not manage Facebook identities, sessions, cookies, proxies, checkpoints, CAPTCHA, or account recovery.
- Review-mode submit uses the same fail-closed composer/submit selector as auto mode; unsupported Facebook layouts may require the operator to post manually and then mark/handle the target outside the auto flow.
- Live Facebook DOM has not been validated in the sandbox.
- Full Flask/release verification must run on the operator Ubuntu environment before auto-submit is enabled.
