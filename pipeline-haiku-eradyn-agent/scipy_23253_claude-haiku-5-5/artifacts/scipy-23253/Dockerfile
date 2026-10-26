# scipy-23253 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/scipy-23253:eradyn-agent validate
#
FROM python:3.13

# both revisions, as the reproduction left them
#   /workspace/buggy  6f1dac31620c69c63ba26a6f2216fc7616824ffc   (+ the developer test patch applied)
#   /workspace/fixed  a6d7f373b5542f4aa1effced6038a8420c302943   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.13.17 (main, Oct 21 2026, 17:49:22) [Clang 23.1.3 ]
#   numpy    2.3.1
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
