# Weekly disk and log hygiene

The Docker host fills up when container logs and old images sit around.
I want the same checks each week, written down while they happen.

Look at free disk space, which containers are using the most log space,
and which images have not been used in 14 days. Propose a cleanup only if
something is actually large. Do not delete anything unless I approve that step.

Do not use `rm -rf`. If a command is refused, suggest a read-only alternative.
