from django.shortcuts import render


def hello(request):
    """Simple hello view to test Tailwind CSS setup."""
    return render(request, "myphoto/hello.html")
