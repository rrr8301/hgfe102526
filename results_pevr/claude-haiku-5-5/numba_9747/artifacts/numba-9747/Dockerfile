# numba-9747 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/numba-9747:latest validate
#
FROM python:3.10

# both revisions, as the reproduction left them
#   /workspace/buggy  30c17f24486ccfb63c2f46c8c8b69db0c9afd889   (+ the developer test patch applied)
#   /workspace/fixed  670ea3e5950d16c808da7044dcc55077d62d9fa3   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.10.22 (main, Oct  9 2026, 15:21:30) [Clang 22.1.3 ]
#   numpy    1.26.4
#   scipy    -
#   arch     x86_64 5.15.0-1081-oracle
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
