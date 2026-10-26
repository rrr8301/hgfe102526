# scikit-learn-32768 — NumBugBench instance, rebuild recipe
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
#   docker run numbugbench/scikit-learn-32768:latest validate
#
FROM python:3.12

# both revisions, as the reproduction left them
#   /workspace/buggy  03c1d566ee268b6029977b816e79d93403568e26   (+ the developer test patch applied)
#   /workspace/fixed  04c9f3eee9dafa76b92bc589e55e700398ca6e10   (+ the developer test patch applied)
#
# environment captured at validation time:
#   python   Python 3.12.15 (main, Oct 21 2026, 17:47:06) [Clang 23.1.3 ]
#   numpy    2.4.0
#   scipy    1.16.3
#   arch     x86_64 5.15.0-1081-oracle
# full lists: env/buggy/requirements.lock, env/fixed/requirements.lock,
#             env/system-packages.txt

COPY scripts/ /benchmark/scripts/
COPY metadata.json /benchmark/metadata.json
RUN mkdir -p /benchmark/candidate && chmod +x /benchmark/scripts/*.sh

ENTRYPOINT ["/benchmark/scripts/entrypoint.sh"]
CMD ["validate"]
