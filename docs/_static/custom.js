/* Anchor links are not working for some reason. Browser is scrolling to the "link" rather than
    the div with that id. Not sure why. This hijacks the event to make it correct
*/
document.addEventListener("DOMContentLoaded",() => {
    //  enable
    window.SphinxRtdTheme.StickyNav.reset = function(){
        console.info("hijacked sticky nav; do nothing")
    }

    top_links = document.querySelectorAll('.document a[href="#"]')
    for (anchor of top_links){
        anchor.addEventListener('click', function (e) {
            v = this.getAttribute("href")
            e.preventDefault();
            history.pushState(null, null, v);
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    }
    anchor_links = document.querySelectorAll('a[href^="#"]');
    for (anchor of anchor_links){
        anchor.addEventListener('click', function (e) {
            v = this.getAttribute("href")
            console.log(v)
            e.preventDefault();
            history.pushState(null, null, v);
            document.getElementById(v.substring(1)).scrollIntoView({
                behavior: 'smooth'
            });
        });
    }
});