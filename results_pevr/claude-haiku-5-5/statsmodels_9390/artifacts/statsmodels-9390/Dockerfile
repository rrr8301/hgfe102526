# statsmodels-9390 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/statsmodels-9390:latest validate
#
FROM python:3.10

# both revisions, as the reproduction left them
#   /workspace/buggy  d0da572b061faf1c691b471e6ad60257b0a3ccf2   (+ the developer test patch applied)
#   /workspace/fixed  9575cf4dee76f929e8492763ce3271243052c7b4   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.10.22 (main, Oct  9 2026, 15:21:30) [Clang 22.1.3 ]
#   numpy    2.1.2
#   scipy    1.14.1
#   arch     x86_64 5.15.0-1081-oracle
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
