# sympy-28567 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/sympy-28567:latest validate
#
FROM python:3.11-slim

# both revisions, as the reproduction left them
#   /workspace/buggy  22779eb67cb200a2dc224f6d04f5e07435b3b66b   (+ the developer test patch applied)
#   /workspace/fixed  48fa24a60d29e3a73113f1bee7d8e854c358bd22   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.11.17 (main, Oct  6 2026, 02:02:21) [GCC 14.2.0]
#   numpy    -
#   scipy    -
#   arch     x86_64 5.15.0-1081-oracle
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
