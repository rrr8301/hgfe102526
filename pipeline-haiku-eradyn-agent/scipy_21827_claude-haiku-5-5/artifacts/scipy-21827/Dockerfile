# scipy-21827 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/scipy-21827:eradyn-agent validate
#
FROM python:3.10

# both revisions, as the reproduction left them
#   /workspace/buggy  c45b3ba3df5ea9ebb517e98d62d138d0f9164724   (+ the developer test patch applied)
#   /workspace/fixed  e5b0af896ef19285677e891f23d338d0b3ba16ed   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.10.22 (main, Oct  9 2026, 15:21:30) [Clang 22.1.3 ]
#   numpy    2.1.3
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
