# numpy-31688 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/numpy-31688:eradyn-agent-gpt validate
#
FROM python:3.13.9

# both revisions, as the reproduction left them
#   /workspace/buggy  7db129300a754b6df58115f744d37fa3b9f0704f   (+ the developer test patch applied)
#   /workspace/fixed  cd1cfb8ef69f07a045180295224cc2fcddf821a1   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.13.5 (main, Aug 10 2026, 12:06:59) [GCC 14.2.0]
#   numpy    -
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
