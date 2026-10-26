# mpmath-1132 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/mpmath-1132:eradyn-agent validate
#
FROM python:3.10-slim

# both revisions, as the reproduction left them
#   /workspace/buggy  ead1a61d094531a6349ffb0b983e76919acaf643   (+ the developer test patch applied)
#   /workspace/fixed  101a8379a8d761ce3dec6718cdb4829c9ef20283   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.10.22 (main, Oct  9 2026, 15:21:30) [Clang 22.1.3 ]
#   numpy    2.2.6
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
