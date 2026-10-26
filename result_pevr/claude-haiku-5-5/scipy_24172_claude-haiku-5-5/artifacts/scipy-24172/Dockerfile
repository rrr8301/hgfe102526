# scipy-24172 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/scipy-24172:latest validate
#
FROM python:3.12

# both revisions, as the reproduction left them
#   /workspace/buggy  1b2e87dbc681b1d540362329f108a32df55bc514   (+ the developer test patch applied)
#   /workspace/fixed  e5ccf400d3affa6924d8e51cb50416a79aae3dd3   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.12.15 (main, Oct  6 2026, 02:02:22) [GCC 14.2.0]
#   numpy    2.3.5
#   scipy    -
#   arch     x86_64 5.4.0-196-generic
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
