# numpy-29522 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/numpy-29522:latest validate
#
FROM python:3.11

# both revisions, as the reproduction left them
#   /workspace/buggy  f6c2bb97146adb8f8e1a65cd6fccd2a12976ba40   (+ the developer test patch applied)
#   /workspace/fixed  0653d084c7182480d317097099d5ad9509a766bc   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.11.17 (main, Oct  6 2026, 04:40:05) [GCC 14.2.0]
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
