# numba-10528 — NumBugBench instance, rebuild recipe
#
# TRANSPARENCY ARTIFACT, NOT THE BUILD THAT PRODUCED THE IMAGE.
#
# The published image is a snapshot of the container in which the reproduction
# was validated, so its environment is the one that actually produced
#     T_dev(buggy) = FAIL   and   T_dev(fixed) = PASS.
# Re-running the steps below resolves dependencies again and may therefore land
# on a DIFFERENT environment — which is the drift this packaging exists to
# avoid. Use this file to understand or modify the instance; use the image to
# reproduce it.
#
#   docker run numbugbench/numba-10528:eradyn-agent validate
#
FROM python:3.14

# both revisions, as the reproduction left them
#   /workspace/buggy  0d0938a717dbe464dfda3448f3f8571d2c606978   (+ the developer test patch applied)
#   /workspace/fixed  43b83d9a0ea3c07108cb73484fcbcc5284615958   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.14.8 (main, Oct 21 2026, 01:05:06) [GCC 14.2.0]
#   numpy    2.5.2
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
