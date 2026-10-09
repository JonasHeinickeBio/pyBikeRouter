import asyncio

import httpx
import pytest
import respx

from bike_routing_agent.providers.brouter_downloads import (
    MAX_SEGMENTS_PER_JOB,
    DownloadBusyError,
    SegmentDownloader,
)

BASE = "https://tiles.example/segments4/"
MB = 2**20


def downloader(tmp_path, **kw) -> SegmentDownloader:
    return SegmentDownloader(tmp_path, base_url=BASE, max_bytes=kw.pop("max_bytes", 50 * MB), **kw)


def serve(name: str, content: bytes, **kw):
    respx.head(f"{BASE}{name}.rd5").mock(
        return_value=httpx.Response(200, headers={"content-length": str(len(content))})
    )
    return respx.get(f"{BASE}{name}.rd5").mock(
        return_value=httpx.Response(200, content=content, **kw)
    )


@respx.mock
async def test_inspect_reports_presence_and_the_size_at_the_source(tmp_path):
    serve("W5_N50", b"x" * 1234)
    respx.head(f"{BASE}E10_N50.rd5").mock(return_value=httpx.Response(404))
    (tmp_path / "E10_N50.rd5").write_bytes(b"have it")
    infos = await downloader(tmp_path).inspect(["W5_N50", "E10_N50", "W5_N50"])
    assert [(i.name, i.present, i.size_bytes) for i in infos] == [
        ("W5_N50", False, 1234),
        ("E10_N50", True, None),  # the source could not say; still reported as present
    ]


@respx.mock
async def test_inspect_survives_an_unreachable_source(tmp_path):
    respx.head(f"{BASE}W5_N50.rd5").mock(side_effect=httpx.ConnectError("down"))
    [info] = await downloader(tmp_path).inspect(["W5_N50"])
    assert info.size_bytes is None and info.present is False


@respx.mock
async def test_a_tile_is_downloaded_completely_and_appears_only_under_its_real_name(tmp_path):
    content = bytes(range(256)) * 5000  # ~1.2 MB, several chunks
    serve("W5_N50", content)
    d = downloader(tmp_path)
    job = d.start(["W5_N50"])
    assert job.state == "running" and job.segments[0].state in ("pending", "downloading")
    await d.wait()
    done = d.job(job.id)
    assert done.state == "done" and done.segments[0].state == "done"
    assert done.segments[0].bytes == done.segments[0].total_bytes == len(content)
    assert (tmp_path / "W5_N50.rd5").read_bytes() == content
    assert not list(tmp_path.glob("*.part"))


@respx.mock
async def test_tiles_that_are_present_are_not_fetched_again(tmp_path):
    (tmp_path / "E10_N50.rd5").write_bytes(b"mine")
    get = serve("W5_N50", b"new")
    other = respx.get(f"{BASE}E10_N50.rd5").mock(return_value=httpx.Response(200, content=b"x"))
    d = downloader(tmp_path)
    job = d.start(["E10_N50", "W5_N50"])
    await d.wait()
    assert [s.state for s in d.job(job.id).segments] == ["present", "done"]
    assert (tmp_path / "E10_N50.rd5").read_bytes() == b"mine"
    assert get.called and not other.called


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"status": 404}, "HTTP 404"),
        ({"status": 302, "headers": {"location": "https://elsewhere.example/x"}}, "HTTP 302"),
        ({"status": 200, "headers": {"transfer-encoding": "chunked"}}, "did not say how big"),
    ],
)
@respx.mock
async def test_a_bad_answer_fails_the_job_and_leaves_no_file(tmp_path, kwargs, reason):
    status = kwargs.pop("status")
    respx.get(f"{BASE}W5_N50.rd5").mock(
        return_value=httpx.Response(status, content=b"" if status != 200 else None, **kwargs)
    )
    d = downloader(tmp_path)
    job = d.start(["W5_N50"])
    await d.wait()
    done = d.job(job.id)
    assert done.state == "failed" and reason in (done.segments[0].error or "")
    assert not list(tmp_path.iterdir())


@respx.mock
async def test_a_tile_over_the_size_limit_is_refused_before_anything_is_stored(tmp_path):
    serve("W5_N50", b"x" * (2 * MB))
    d = downloader(tmp_path, max_bytes=1 * MB)
    job = d.start(["W5_N50"])
    await d.wait()
    assert "over the 1 MB limit" in d.job(job.id).segments[0].error
    assert not list(tmp_path.iterdir())


@respx.mock
async def test_not_enough_disk_space_is_refused_before_anything_is_stored(tmp_path, monkeypatch):
    serve("W5_N50", b"x" * 1000)
    d = downloader(tmp_path)
    monkeypatch.setattr(d, "free_bytes", lambda: 10)
    job = d.start(["W5_N50"])
    await d.wait()
    assert "not enough free disk space" in d.job(job.id).segments[0].error
    assert not list(tmp_path.iterdir())


@respx.mock
async def test_a_source_that_sends_more_than_it_announced_is_rejected(tmp_path):
    class Greedy(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"x" * 150

    respx.get(f"{BASE}W5_N50.rd5").mock(
        return_value=httpx.Response(200, stream=Greedy(), headers={"content-length": "100"})
    )
    d = downloader(tmp_path)
    job = d.start(["W5_N50"])
    await d.wait()
    done = d.job(job.id)
    assert done.state == "failed" and not list(tmp_path.iterdir())


@respx.mock
async def test_a_failure_stops_the_job_and_marks_the_rest_as_not_downloaded(tmp_path):
    respx.get(f"{BASE}W5_N50.rd5").mock(return_value=httpx.Response(500))
    serve("E10_N50", b"fine")
    d = downloader(tmp_path)
    job = d.start(["W5_N50", "E10_N50"])
    await d.wait()
    done = d.job(job.id)
    assert done.state == "failed" and "HTTP 500" in done.error
    assert [s.state for s in done.segments] == ["failed", "failed"]
    assert not (tmp_path / "E10_N50.rd5").exists()


@respx.mock
async def test_only_one_download_runs_at_a_time(tmp_path):
    gate = asyncio.Event()

    class Slow(httpx.AsyncByteStream):
        async def __aiter__(self):
            await gate.wait()
            yield b"x" * 10

    respx.get(f"{BASE}W5_N50.rd5").mock(
        return_value=httpx.Response(200, stream=Slow(), headers={"content-length": "10"})
    )
    d = downloader(tmp_path)
    d.start(["W5_N50"])
    await asyncio.sleep(0.05)
    with pytest.raises(DownloadBusyError):
        d.start(["E10_N50"])
    gate.set()
    await d.wait()
    serve("E10_N50", b"ok")
    d.start(["E10_N50"])  # free again
    await d.wait()


async def test_only_well_formed_tile_names_in_a_bounded_number_are_accepted(tmp_path):
    d = downloader(tmp_path)
    for bad in (["../W5_N50"], ["W5_N50.rd5"], ["w5_n50"], ["W5_N50; rm -rf /"], [], [""]):
        with pytest.raises(ValueError):
            d.start(bad)
        with pytest.raises(ValueError):
            await d.inspect(bad)
    too_many = [f"E{5 * i}_N50" for i in range(MAX_SEGMENTS_PER_JOB + 1)]
    with pytest.raises(ValueError):
        d.start(too_many)
    assert d.job("nope") is None
