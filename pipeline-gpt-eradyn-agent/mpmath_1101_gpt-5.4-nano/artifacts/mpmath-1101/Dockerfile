# mpmath-1101 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/mpmath-1101:eradyn-agent-gpt validate
#
FROM python:3.11-slim

# both revisions, as the reproduction left them
#   /workspace/buggy  e45f046a02e3ec92a3ee38512253b37f89546cf8   (+ the developer test patch applied)
#   /workspace/fixed  29c496bf1419f74902bfbc597b28c01897af50b6   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.11.17 (main, Oct  6 2026, 02:02:21) [GCC 14.2.0]
#   numpy    2.4.6
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
